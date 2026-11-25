"""Referencia y TWAP público de Polymarket. Sin credenciales de inferencia."""
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import math
import sqlite3
import time
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen

SOURCE = "https://polymarket.com/api/crypto/crypto-price"
STREAM = "wss://ws-live-data.polymarket.com"
TOPIC = "crypto_prices_twap_sixty"
QUOTE_SOURCE = "Polymarket RTDS · Chainlink TWAP 60 s"


def tables(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS btc_targets(start INTEGER PRIMARY KEY,seen REAL,price REAL,source TEXT);
        CREATE TABLE IF NOT EXISTS btc_target_status(id INTEGER PRIMARY KEY CHECK(id=1),at REAL,detail TEXT);
        CREATE TABLE IF NOT EXISTS btc_quotes(ts REAL PRIMARY KEY,seen REAL,price REAL,source TEXT);
        CREATE TABLE IF NOT EXISTS btc_twap_quotes(
            ts REAL PRIMARY KEY,seen REAL,price REAL,source TEXT,raw_value TEXT);
        CREATE TABLE IF NOT EXISTS btc_feed_status(
            id INTEGER PRIMARY KEY CHECK(id=1),state TEXT,at REAL,last_ok REAL,detail TEXT);
        CREATE TABLE IF NOT EXISTS yahoo_price_predictions(
            start INTEGER PRIMARY KEY,ready REAL,price REAL,anchor REAL,model TEXT,detail TEXT);
    """)


def feed_status(db, state, detail, success=False):
    now = time.time()
    with db:
        db.execute("""INSERT INTO btc_feed_status VALUES(1,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET state=excluded.state,at=excluded.at,
                last_ok=coalesce(excluded.last_ok,btc_feed_status.last_ok),detail=excluded.detail""",
                   (state,now,now if success else None,detail))


def twap_points(message, observed):
    """Solo TWAP BTC/USD de 60 s. La precisión E18 corresponde a RTDS, no a PolyBolt."""
    if not isinstance(message,dict) or message.get("topic") != TOPIC or message.get("type") not in ("update","subscribe"):
        return []
    payload = message.get("payload")
    if not isinstance(payload,dict) or payload.get("symbol") != "btc/usd" or payload.get("window_s") != 60:
        return []
    records = payload.get("data") if message["type"] == "subscribe" else [payload]
    if not isinstance(records,list) or len(records) > 180:
        return []
    result = {}
    for row in records:
        try:
            if not isinstance(row,dict) or isinstance(row.get("timestamp"),bool):
                continue
            ts = float(row["timestamp"])/1000
            # RTDS full_accuracy_value es el precio multiplicado por 10**18.
            full = row.get("full_accuracy_value")
            price = Decimal(str(full))/Decimal(10**18) if full is not None else Decimal(str(row["value"]))
            if not math.isfinite(ts) or not price.is_finite() or price <= 0 or not math.isfinite(float(price)) or not observed-120 <= ts <= observed+2:
                continue
            if full is not None and "value" in row and not math.isclose(float(price),float(row["value"]),rel_tol=1e-8):
                continue
            result[ts] = (ts,observed,float(price),QUOTE_SOURCE,str(price))
        except (KeyError,TypeError,ValueError,InvalidOperation,OverflowError):
            continue
    return sorted(result.values())


def store_twap(db, message, observed):
    points = twap_points(message,observed)
    if not points:
        return None
    with db:
        db.executemany("INSERT OR IGNORE INTO btc_twap_quotes VALUES(?,?,?,?,?)",points)
        db.execute("DELETE FROM btc_twap_quotes WHERE ts<?",(observed-86400,))
    return points[-1][0]


def stream(path, stop):
    """Una conexión ligera, latido cada 5 s y reconexión con espera creciente."""
    with closing(sqlite3.connect(path,timeout=10)) as db:
        try:
            import websocket
        except ImportError:
            feed_status(db,"error","Instala las dependencias actualizadas de requirements-lock.txt")
            return
        retry = 1
        while not stop.is_set():
            try:
                feed_status(db,"waiting","Conectando con el TWAP público de Polymarket")
                # Destino fijo, TLS validado y sin encabezados de autenticación.
                with closing(websocket.create_connection(STREAM,timeout=10,suppress_origin=True,redirect_limit=0)) as ws:
                    ws.settimeout(1)
                    # El filtro compacto es necesario también para recibir updates,
                    # no solo la instantánea inicial del servidor RTDS.
                    ws.send(json.dumps({"action":"subscribe","subscriptions":[
                        {"topic":TOPIC,"type":"*","filters":'{"symbol":"btc/usd"}'}]}))
                    last_ping,last_price = time.monotonic(),time.monotonic()
                    while not stop.is_set():
                        now = time.monotonic()
                        if now-last_ping >= 5:
                            ws.send("PING")
                            last_ping = now
                        if now-last_price > 20:
                            raise ValueError("Sin cotizaciones recientes")
                        try:
                            raw = ws.recv()
                        except websocket.WebSocketTimeoutException:
                            continue
                        if not ws.connected:
                            raise ValueError("Flujo cerrado")
                        if raw in ("","PONG"):
                            continue
                        if not isinstance(raw,str) or len(raw) > 262144:
                            raise ValueError("Formato inesperado del flujo")
                        latest = store_twap(db,json.loads(raw),time.time())
                        if latest is not None and time.time()-latest <= 10:
                            last_price,retry = time.monotonic(),1
                            feed_status(db,"ok","BTC/USD recibido de Polymarket · Chainlink TWAP 60 s",True)
            except (OSError,ValueError,TypeError,sqlite3.Error,websocket.WebSocketException):
                feed_status(db,"error","Flujo BTC de Polymarket interrumpido. Reconectando, sin sustitución por Yahoo")
            if stop.is_set() or stop.wait(retry):
                break
            retry = min(30,retry*2)
        feed_status(db,"stopped","Flujo BTC detenido")


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
