"""Referencia y TWAP público de Polymarket. Sin credenciales de inferencia."""
from contextlib import closing
from decimal import Decimal, InvalidOperation
import json
import math
import sqlite3
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

SOURCE = "https://polymarket.com/api/crypto/crypto-price"
GAMMA_SOURCE = "https://gamma-api.polymarket.com/events/slug/"
OPENING_TWAP_SOURCE = "Polymarket RTDS · Chainlink TWAP 60 s · apertura exacta"
STREAM = "wss://ws-live-data.polymarket.com"
TOPIC = "crypto_prices_twap_sixty"
QUOTE_SOURCE = "Polymarket RTDS · Chainlink TWAP 60 s"


def tables(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS btc_targets(start INTEGER PRIMARY KEY,seen REAL,price REAL,source TEXT);
        CREATE TABLE IF NOT EXISTS btc_target_status(id INTEGER PRIMARY KEY CHECK(id=1),at REAL,detail TEXT);
        CREATE TABLE IF NOT EXISTS btc_target_meta(start INTEGER PRIMARY KEY,kind TEXT,reference_ts REAL,checked_at REAL);
        CREATE TABLE IF NOT EXISTS btc_target_revisions(id INTEGER PRIMARY KEY,start INTEGER,seen REAL,
            price REAL,source TEXT,previous_price REAL);
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
    expected_slug = "btc-updown-5m-"+str(start)
    request = Request(GAMMA_SOURCE+expected_slug,headers={"Accept":"application/json","User-Agent":"BTC-Research/1.0","Cache-Control":"no-cache"})
    try:
        with urlopen(request,timeout=10) as response:
            raw = json.load(response)
    except HTTPError as exc:
        exc.close()
        raise ValueError("Consulta pública de referencia no disponible") from None
    if not isinstance(raw,dict):
        raise ValueError("Formato público de referencia inesperado")
    if raw.get("slug") != expected_slug:
        raise ValueError("Gamma devolvió una referencia de otro intervalo")
    metadata=raw.get("eventMetadata")
    value = metadata.get("priceToBeat") if isinstance(metadata,dict) else None
    if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value) or value <= 0:
        raise ValueError("Polymarket aún no publica el precio a superar")
    return float(value)


def refresh_target(db,start):
    observed=time.time()
    try:
        price=target(start)
        source,kind=GAMMA_SOURCE,"confirmed"
    except (OSError,ValueError,TypeError,KeyError):
        # No se aproxima con el primer tick posterior ni con un open spot.
        exact=db.execute("SELECT price FROM btc_twap_quotes WHERE ts=? AND seen<=?",(start,observed)).fetchone()
        if exact is None:
            return False
        price,source,kind=exact[0],OPENING_TWAP_SOURCE,"opening_twap"
    old=db.execute("SELECT price,source FROM btc_targets WHERE start=?",(start,)).fetchone()
    if old and old[1]==GAMMA_SOURCE and source!=GAMMA_SOURCE:
        return True
    with db:
        if old is None or old[0]!=price or old[1]!=source:
            db.execute("INSERT INTO btc_target_revisions(start,seen,price,source,previous_price) VALUES(?,?,?,?,?)",
                       (start,observed,price,source,old[0] if old else None))
            db.execute("INSERT OR REPLACE INTO btc_targets VALUES(?,?,?,?)",(start,observed,price,source))
        db.execute("INSERT OR REPLACE INTO btc_target_meta VALUES(?,?,?,?)",(start,kind,start,observed))
        db.execute("INSERT OR REPLACE INTO btc_target_status VALUES(1,?,?)",(observed,
                   "Referencia confirmada por Gamma" if kind=="confirmed" else "TWAP exacto de apertura; esperando confirmación de Gamma"))
    return True


def collect(path, stop):
    with closing(sqlite3.connect(path,timeout=10)) as db:
        while not stop.is_set():
            start = int(time.time())//300*300
            if not refresh_target(db,start):
                with db:
                    db.execute("INSERT OR REPLACE INTO btc_target_status VALUES(1,?,?)",
                               (time.time(),"Esperando precio a superar del intervalo exacto; no se usa openPrice spot"))
            # Reconciliar también el intervalo anterior cuando Gamma publica su referencia.
            previous=start-300
            row=db.execute("SELECT source FROM btc_targets WHERE start=?",(previous,)).fetchone()
            if row and row[0]!=GAMMA_SOURCE:
                refresh_target(db,previous)
            phase=time.time()%300
            stop.wait(min(2 if phase<15 else 15,300-phase))
