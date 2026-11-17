"""Inspección pública de BTC 5m. No contiene autenticación ni envío de órdenes."""
import argparse
import json
import math
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"


class DataError(ValueError):
    pass


def get_json(base, path, params=None):
    if base not in (GAMMA, CLOB) or not path.startswith("/"):
        raise DataError("Destino no permitido")
    url = base + path + ("?" + urlencode(params) if params else "")
    for attempt in range(3):
        try:
            request = Request(url, headers={"User-Agent": "BTC5mResearch/0.1", "Accept": "application/json"})
            with urlopen(request, timeout=12) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(2 ** attempt)
    raise RuntimeError("Reintentos agotados")


def array(value, field):
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError as exc:
        raise DataError(f"{field}: JSON inválido") from exc
    if not isinstance(parsed, list):
        raise DataError(f"{field}: se esperaba una lista")
    return parsed


def inspect_market(event, slug):
    match = re.fullmatch(r"btc-updown-5m-(\d+)", slug)
    if not match:
        raise DataError("Se requiere el slug btc-updown-5m-<timestamp>")
    if not isinstance(event, dict) or event.get("slug") != slug:
        raise DataError("El evento no coincide con el solicitado")
    candidates = [m for m in event.get("markets", []) if isinstance(m, dict) and m.get("slug") == slug]
    if len(candidates) != 1:
        raise DataError("No hay un único mercado con el slug solicitado")
    market = candidates[0]
    start = int(match.group(1))
    if start % 300:
        raise DataError("Inicio no alineado a intervalos de cinco minutos")
    try:
        end = datetime.fromisoformat(market["endDate"].replace("Z", "+00:00"))
        if end.tzinfo is None or end.timestamp() != start + 300:
            raise DataError("El cierre del mercado no coincide con inicio + 300 segundos")
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise DataError(f"Fecha de cierre inválida: {exc}") from exc
    labels = array(market.get("outcomes"), "outcomes")
    if len(labels) != 2 or set(labels) != {"Up", "Down"}:
        raise DataError("El mercado no tiene exactamente Up y Down")
    version = market.get("version")
    if version not in (None, "v1", "v2"):
        raise DataError(f"Versión no soportada: {version}")
    field = "positionIds" if version == "v2" else "clobTokenIds"
    identifiers = array(market.get(field), field)
    if len(identifiers) != 2 or any(not isinstance(x, str) or not x.isdigit() for x in identifiers):
        raise DataError("Identificadores de resultados inválidos")
    if len(set(identifiers)) != 2:
        raise DataError("Identificadores duplicados")
    if not isinstance(market.get("description"), str) or not market["description"].strip():
        raise DataError("Faltan reglas de resolución")
    return {
        "slug": slug, "question": market.get("question"), "version": version,
        "id_field": field, "outcome_ids": dict(zip(labels, identifiers)),
        "window_start_utc": datetime.fromtimestamp(start, timezone.utc).isoformat(),
        "window_end_utc": end.isoformat(), "description": market["description"],
        "resolution_source": market.get("resolutionSource") or event.get("resolutionSource"),
        "active": market.get("active"), "closed": market.get("closed"),
        "accepting_orders": market.get("acceptingOrders"),
        "rules_approved_for_training": False,
    }


def inspect_book(book, asset_id):
    if not isinstance(book, dict) or book.get("asset_id") != asset_id:
        raise DataError("El libro no corresponde al resultado solicitado")
    sides = {}
    for side in ("bids", "asks"):
        if not isinstance(book.get(side), list):
            raise DataError(f"Libro sin {side}")
        prices = []
        for level in book[side]:
            try:
                price, size = float(level["price"]), float(level["size"])
            except (KeyError, TypeError, ValueError) as exc:
                raise DataError("Nivel de libro inválido") from exc
            if not math.isfinite(price) or not math.isfinite(size) or not 0 <= price <= 1 or size <= 0:
                raise DataError("Precio o tamaño de libro fuera de rango")
            prices.append(price)
        sides[side] = prices
    bid = max(sides["bids"], default=None)
    ask = min(sides["asks"], default=None)
    if bid is not None and ask is not None and bid > ask:
        raise DataError("Libro cruzado; repetir lectura antes de usarlo")
    return {"best_bid": bid, "best_ask": ask,
            "spread": ask - bid if bid is not None and ask is not None else None,
            "timestamp": book.get("timestamp"), "bid_levels": len(sides["bids"]),
            "ask_levels": len(sides["asks"])}


def capture(slug):
    event = get_json(GAMMA, "/events/slug/" + slug)
    market = inspect_market(event, slug)
    books, summaries, errors = {}, {}, []
    for label, asset_id in market["outcome_ids"].items():
        try:
            book = get_json(CLOB, "/book", {"token_id": asset_id})
            summaries[label] = inspect_book(book, asset_id)
            books[label] = book
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            errors.append({"stage": "orderbook", "outcome": label, "error": str(exc)})
    return {"captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "mode": "read_only", "market": market, "books": summaries,
            "errors": errors, "raw_event": event, "raw_books": books,
            "connection_ok": len(summaries) == 2 and not errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", help="Omitir para inspeccionar el intervalo actual")
    parser.add_argument("--output", default="data/public_snapshot.json")
    args = parser.parse_args()
    slug = args.slug or f"btc-updown-5m-{int(time.time()) // 300 * 300}"
    # Validar antes de construir una URL.
    if not re.fullmatch(r"btc-updown-5m-\d+", slug):
        parser.error("Slug BTC 5m inválido")
    try:
        result = capture(slug)
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        result = {"captured_at_utc": datetime.now(timezone.utc).isoformat(),
                  "mode": "read_only", "slug": slug, "connection_ok": False,
                  "errors": [{"stage": "market", "error": str(exc)}]}
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("connection_ok", "errors")}, ensure_ascii=True))
    print(f"Snapshot: {path.resolve()}")
    return 0 if result["connection_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
