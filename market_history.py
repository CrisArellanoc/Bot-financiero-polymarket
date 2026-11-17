"""Backfill público auditable. Conserva tiempo de observación y cierre reportado.

eventMetadata se observó empíricamente en Gamma: no se presume disponible antes
de su primera lectura ni se equipara closedTime con su fecha de publicación.
"""
import argparse
import hashlib
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from polymarket_public import GAMMA, get_json, inspect_market, array, DataError


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def parse_time(text):
    value = datetime.fromisoformat(text.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise DataError('Timestamp sin zona horaria')
    return value.timestamp()


def normalize_resolved(event, observed_at):
    summary = inspect_market(event, event['slug'])
    market = next(m for m in event['markets'] if m.get('slug') == event['slug'])
    if market.get('closed') is not True or market.get('umaResolutionStatus') != 'resolved':
        raise DataError('Mercado sin resolución final confirmada por Gamma')
    labels = array(market.get('outcomes'), 'outcomes')
    payouts = [float(x) for x in array(market.get('outcomePrices'), 'outcomePrices')]
    if len(payouts) != 2 or sorted(payouts) != [0.0, 1.0]:
        raise DataError('Pagos finales no binarios o ambiguos')
    winner = labels[payouts.index(1.0)]
    metadata = event.get('eventMetadata') or {}
    try:
        opening, final = float(metadata['priceToBeat']), float(metadata['finalPrice'])
    except (KeyError, ValueError, TypeError) as exc:
        raise DataError('Faltan referencias de precio válidas') from exc
    if not all(math.isfinite(p) and p > 0 for p in (opening, final)):
        raise DataError('Referencias de precio no positivas o no finitas')
    if winner != ('Up' if final >= opening else 'Down'):
        raise DataError('Referencia final y resultado oficial no coinciden')
    start, end = parse_time(summary['window_start_utc']), parse_time(summary['window_end_utc'])
    closed = parse_time(market['closedTime'])
    observed = parse_time(observed_at)
    if not end <= closed <= observed:
        raise DataError('Cierre reportado prematuro o futuro')
    return dict(slug=event['slug'], market_id=market['id'], condition_id=market['conditionId'],
                start=iso(start), end=iso(end), reported_closed_at=iso(closed),
                metadata_first_observed_at=observed_at, label_first_observed_at=observed_at,
                price_to_beat=opening, final_price=final, target_price_difference=final-opening,
                outcome=winner, y_up=int(winner == 'Up'), resolution_final=True,
                resolution_source=summary['resolution_source'],
                rules_sha256=hashlib.sha256(summary['description'].encode()).hexdigest(),
                timing_evidence='historical_metadata_observed_now_not_point_in_time_history')


def collect(intervals, last_start, output):
    if not 1 <= intervals <= 100000 or last_start % 300:
        raise ValueError('Intervalos inválidos o inicio no alineado')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    starts = list(range(last_start - (intervals-1)*300, last_start+1, 300))
    requested = [f'btc-updown-5m-{t}' for t in starts]
    records, failures, retrieved = [], [], set()
    for page_no, offset in enumerate(range(0, len(requested), 40)):
        slugs = requested[offset:offset+40]
        params = [('slug', slug) for slug in slugs] + [('limit', str(len(slugs)))]
        observed = iso(time.time())
        try:
            data = get_json(GAMMA, '/events', params)
            observed = iso(time.time())
            if not isinstance(data, list):
                raise DataError('Respuesta /events no es lista')
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            failures.extend({'slug': slug, 'reason': str(exc)} for slug in slugs)
            continue
        raw = dict(observed_at=observed, query=params, events=data)
        raw_path = output / f'raw_{page_no:04d}.json'
        raw_path.write_text(json.dumps(raw, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        returned = set()
        for event in data:
            slug = event.get('slug')
            if slug not in slugs or slug in retrieved:
                failures.append({'slug': slug, 'reason': 'Evento inesperado o duplicado'})
                continue
            retrieved.add(slug)
            returned.add(slug)
            try:
                record = normalize_resolved(event, observed)
                record['raw_file'] = raw_path.name
                records.append(record)
            except (ValueError, KeyError, TypeError, StopIteration) as exc:
                failures.append({'slug': slug, 'reason': str(exc)})
        failures.extend({'slug': slug, 'reason': 'No devuelto por Gamma'} for slug in set(slugs)-returned)
        print(json.dumps({'processed': min(offset+40, intervals), 'requested': intervals,
                          'valid': len(records), 'issues': len(failures)}), flush=True)
        time.sleep(0.1)
    records.sort(key=lambda r: r['start'])
    (output / 'records.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False, allow_nan=False)+'\n' for r in records), encoding='utf-8')
    gaps = sum(parse_time(b['start'])-parse_time(a['start']) != 300 for a, b in zip(records, records[1:]))
    summary = dict(created_at=iso(time.time()), requested=intervals, received=len(retrieved),
                   valid=len(records), gaps=gaps, issues=failures,
                   first_start=records[0]['start'] if records else None,
                   last_start=records[-1]['start'] if records else None,
                   outcomes={label: sum(r['outcome']==label for r in records) for label in ('Up','Down')},
                   timing_note='reported_closed_at is not proven publication time; first observed timestamps retained')
    (output / 'audit.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding='utf-8')
    return summary


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--intervals', type=int, default=2016)
    p.add_argument('--last-start', type=int, default=None)
    p.add_argument('--output', required=True)
    args = p.parse_args()
    report = collect(args.intervals, args.last_start if args.last_start is not None else int(time.time())//300*300-300, args.output)
    print(json.dumps({k:v for k,v in report.items() if k != 'issues'}, indent=2))
