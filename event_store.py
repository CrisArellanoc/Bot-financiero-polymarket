"""Almacén SQLite auditable de eventos del mercado solicitado y referencias.

Conserva hora de recepción original, descarta eventos de otros mercados y
deduplica por contenido. No interpreta un precio cercano a 1 como resolución.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import zlib
from market_history import parse_time
from polymarket_public import inspect_book


class EventStore:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS markets (
            slug TEXT PRIMARY KEY, condition_id TEXT UNIQUE NOT NULL,
            up_id TEXT NOT NULL, down_id TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS events (
            hash TEXT PRIMARY KEY, slug TEXT NOT NULL, type TEXT NOT NULL,
            received_at TEXT NOT NULL, source_timestamp TEXT, raw_zlib BLOB NOT NULL);
          CREATE INDEX IF NOT EXISTS event_time ON events(slug,received_at);
          CREATE TABLE IF NOT EXISTS history_versions (
            hash TEXT PRIMARY KEY, slug TEXT NOT NULL, first_observed_at TEXT NOT NULL,
            reported_closed_at TEXT NOT NULL, raw_json TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS rejected (
            hash TEXT PRIMARY KEY, reason TEXT NOT NULL, received_at TEXT NOT NULL);
        ''')

    def register(self, snapshot):
        market = snapshot['market']
        raw = next(m for m in snapshot['raw_event']['markets'] if m['slug'] == market['slug'])
        values = (market['slug'], raw['conditionId'], market['outcome_ids']['Up'], market['outcome_ids']['Down'],
                  market['window_start_utc'], market['window_end_utc'])
        old = self.db.execute('SELECT * FROM markets WHERE slug=?', (values[0],)).fetchone()
        if old and old != values:
            raise ValueError('Identidad del mercado cambió; requiere revisión')
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO markets VALUES (?,?,?,?,?,?)', values)
        return values

    def import_stream(self, snapshot_path, stream_path):
        snapshot = json.loads(Path(snapshot_path).read_text(encoding='utf-8'))
        slug, condition, up, down, _, _ = self.register(snapshot)
        allowed_assets = {up, down}
        counts = {'stored': 0, 'duplicate': 0, 'foreign': 0, 'rejected': 0, 'heartbeats': 0}
        with self.db, open(stream_path, encoding='utf-8') as handle:
            for line in handle:
                row = json.loads(line)
                if row.get('kind') == 'heartbeat':
                    counts['heartbeats'] += 1
                if row.get('kind') != 'frame':
                    continue
                received = row['received_at_utc']
                parse_time(received)
                payload = json.loads(row['raw'])
                for event in payload if isinstance(payload, list) else [payload]:
                    raw = json.dumps(event, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
                    key = hashlib.sha256(raw).hexdigest()
                    if event.get('market') != condition:
                        counts['foreign'] += 1
                        continue
                    try:
                        kind = event['event_type']
                        if kind == 'book':
                            if event['asset_id'] not in allowed_assets:
                                raise ValueError('Identificador no pertenece al mercado')
                            inspect_book(event, event['asset_id'])
                        elif kind == 'price_change':
                            changes = event.get('price_changes')
                            if not isinstance(changes, list) or not changes or any(c.get('asset_id') not in allowed_assets for c in changes):
                                raise ValueError('Actualizaciones con identificadores inválidos')
                        elif kind == 'market_resolved':
                            if event.get('winning_asset_id') not in allowed_assets:
                                raise ValueError('Ganador no corresponde al mercado')
                        elif kind in ('last_trade_price', 'best_bid_ask', 'tick_size_change'):
                            if event.get('asset_id') not in allowed_assets:
                                raise ValueError('Identificador no pertenece al mercado')
                        else:
                            raise ValueError('Tipo no soportado para este dataset')
                        timestamp = event.get('timestamp')
                        if not isinstance(timestamp, (str, int)) or not str(timestamp).isdigit():
                            raise ValueError('Timestamp de origen inválido')
                        # El timestamp de recepción se conserva aunque haya desajuste de reloj;
                        # un consumidor debe verificar antigüedad antes de operar.
                        changed = self.db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?)',
                            (key, slug, kind, received, str(timestamp), zlib.compress(raw))).rowcount
                        if changed:
                            counts['stored'] += 1
                        else:
                            counts['duplicate'] += 1
                            self.db.execute('UPDATE events SET received_at=min(received_at,?) WHERE hash=?', (received,key))
                    except (ValueError, KeyError, TypeError) as exc:
                        counts['rejected'] += 1
                        self.db.execute('INSERT OR IGNORE INTO rejected VALUES (?,?,?)', (key, str(exc), received))
        return counts

    def import_history(self, records_path):
        count = 0
        with self.db:
            for line in Path(records_path).read_text(encoding='utf-8').splitlines():
                row = json.loads(line)
                observed = row['metadata_first_observed_at']
                parse_time(observed)
                # Observaciones repetidas de los mismos valores preservan su primera fecha.
                facts = {k:v for k,v in row.items() if k not in ('raw_file','metadata_first_observed_at','label_first_observed_at')}
                key = hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()
                count += self.db.execute('INSERT OR IGNORE INTO history_versions VALUES (?,?,?,?,?)',
                    (key, row['slug'], observed, row['reported_closed_at'], json.dumps(row))).rowcount
                self.db.execute('UPDATE history_versions SET first_observed_at=min(first_observed_at,?) WHERE hash=?', (observed,key))
        return count

    def close(self):
        self.db.close()


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db', required=True)
    p.add_argument('--snapshot')
    p.add_argument('--stream')
    p.add_argument('--history')
    args = p.parse_args()
    if bool(args.snapshot) != bool(args.stream): p.error('--snapshot y --stream deben usarse juntos')
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    store = EventStore(args.db)
    try:
        if args.history: print(json.dumps({'history_inserted': store.import_history(args.history)}))
        if args.stream: print(json.dumps(store.import_stream(args.snapshot, args.stream)))
    finally:
        store.close()
