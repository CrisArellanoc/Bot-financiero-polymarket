"""Referencia pública del mercado. Nunca recibe credenciales de inferencia."""
from contextlib import closing
from datetime import datetime, timezone
import json
import math
import sqlite3
import time
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen

SOURCE = "https://polymarket.com/api/crypto/crypto-price"


def tables(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS btc_targets(start INTEGER PRIMARY KEY,seen REAL,price REAL,source TEXT);
        CREATE TABLE IF NOT EXISTS btc_target_status(id INTEGER PRIMARY KEY CHECK(id=1),at REAL,detail TEXT);
        CREATE TABLE IF NOT EXISTS btc_quotes(ts REAL PRIMARY KEY,seen REAL,price REAL,source TEXT);
        CREATE TABLE IF NOT EXISTS yahoo_price_predictions(
            start INTEGER PRIMARY KEY,ready REAL,price REAL,anchor REAL,model TEXT,detail TEXT);
    """)


def target(start):
    if start % 300:
        raise ValueError("Intervalo BTC inválido")
    iso = lambda t: datetime.fromtimestamp(t,timezone.utc).isoformat().replace("+00:00","Z")
    query = urlencode(dict(symbol="BTC",eventStartTime=iso(start),endDate=iso(start+300),variant="fiveminute"))
    request = Request(SOURCE+"?"+query,headers={"Accept":"application/json","User-Agent":"BTC-Research/1.0"})
    try:
        with urlopen(request,timeout=10) as response:
            raw = json.load(response)
    except HTTPError as exc:
        exc.close()
        raise ValueError("Consulta pública de referencia no disponible") from None
    if not isinstance(raw,dict):
        raise ValueError("Formato público de referencia inesperado")
    value = raw.get("openPrice")
    if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value) or value <= 0:
        raise ValueError("Polymarket aún no publica el precio a superar")
    return float(value)


def collect(path, stop):
    with closing(sqlite3.connect(path,timeout=10)) as db:
        while not stop.is_set():
            start = int(time.time())//300*300
            if not db.execute("SELECT 1 FROM btc_targets WHERE start=?",(start,)).fetchone():
                try:
                    price = target(start)
                    seen = time.time()
                    with db:
                        db.execute("INSERT OR IGNORE INTO btc_targets VALUES(?,?,?,?)",(start,seen,price,SOURCE))
                        db.execute("INSERT OR REPLACE INTO btc_target_status VALUES(1,?,?)",
                                   (seen,"Referencia publicada por Polymarket"))
                except (OSError,ValueError,TypeError,KeyError):
                    with db:
                        db.execute("INSERT OR REPLACE INTO btc_target_status VALUES(1,?,?)",
                                   (time.time(),"Referencia pública pendiente. Se reintentará"))
            stop.wait(min(10,300-time.time()%300))
