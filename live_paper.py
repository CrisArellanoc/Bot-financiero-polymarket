"""Agente prospectivo BTC 5m. Datos públicos y contabilidad simulada en SQLite."""
import argparse
from contextlib import closing, contextmanager
from decimal import Decimal, ROUND_UP
from functools import partial
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
from threading import Event, Thread
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import live_model as ml
import local_observer
import laya_shadow
import service_status
import yahoo_predictor
import btc_reference
from remote_models import RemoteLaya, RemoteSupervisor, RemoteModelError
from settings import load_settings
from autonomy import Limits, PaperLedger, number
from market_history import iso, normalize_resolved
from polymarket_public import CLOB, GAMMA, DataError, get_json, inspect_book, inspect_market

RULE_HASH = "485ceb1dabc4aa12fb42c76184563b7378f01e5de9ded73df049c0d191cd5ad1"
SOURCE = "https://data.chain.link/streams/btc-usd-twap-60s-streams"
LIMITS = Limits("15", "30", "35", "0.03", "0.05", 12, 15)
SHARES = Decimal(20)
API_ERRORS = (ValueError, KeyError, TypeError, IndexError, HTTPError, URLError, TimeoutError, OSError)


def dump(value):
    return json.dumps(value, allow_nan=False, ensure_ascii=False)


def slug(start):
    return f"btc-updown-5m-{start}"


@contextmanager
def writer_lock(path):
    """Un proceso por cartera, incluso cuando usan puertos distintos."""
    with open(str(path) + ".lock", "a+b") as handle:
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise SystemExit("Otro proceso usa esta cartera o su bloqueo no está disponible") from exc
        yield


def market_for(start):
    event = get_json(GAMMA, "/events/slug/" + slug(start))
    view = inspect_market(event, slug(start))
    digest = hashlib.sha256(view["description"].encode()).hexdigest()
    if digest != RULE_HASH or view["resolution_source"] != SOURCE:
        raise DataError("Las reglas BTC/TWAP cambiaron y requieren revisión")
    raw = next(m for m in event["markets"] if m.get("slug") == view["slug"])
    return event, view, raw


def must_be_open(view):
    if view["active"] is not True or view["closed"] is not False or view["accepting_orders"] is not True:
        raise DataError("El mercado no acepta órdenes")


def orderbook(view, side):
    token = view["outcome_ids"][side]
    book = get_json(CLOB, "/book", {"token_id": token})
    observed = time.time()
    top = inspect_book(book, token)
    if top["best_bid"] is None or top["best_ask"] is None or not 0 < top["best_bid"] <= top["best_ask"] < 1:
        raise DataError("Faltan cotizaciones válidas de ambos lados")
    timestamp = float(book["timestamp"]) / 1000
    if not -ml.CLOCK_TOLERANCE <= observed - timestamp <= ml.MAX_INPUT_AGE:
        raise DataError("Cotización CLOB vencida o futura")
    return book, top, observed, timestamp


def costs(raw, book, limit):
    """Comisión taker de los niveles ejecutables y mínimos vigentes del mercado."""
    rate = Decimal(0)
    if raw.get("feesEnabled") is not False:
        schedule = raw.get("feeSchedule") or {}
        if raw.get("feesEnabled") is not True or schedule.get("exponent") != 1 or schedule.get("takerOnly") is not True:
            raise DataError("Comisión no verificada para este mercado")
        rate = number(schedule["rate"])
        if not 0 < rate < 1:
            raise DataError("Tasa taker inválida")
    left, gross, fee = SHARES, Decimal(0), Decimal(0)
    for price, size in sorted((number(v["price"]), number(v["size"])) for v in book["asks"]):
        if price > limit or left == 0:
            break
        qty = min(left, size)
        left -= qty
        gross += qty * price
        fee += qty * rate * price * (1-price)
    if left:
        raise DataError("Profundidad insuficiente al precio límite")
    minimum = number(raw["orderMinSize"])
    if minimum <= 0 or gross < minimum:
        raise DataError("Importe inferior al mínimo publicado del mercado")
    return str(fee.quantize(Decimal(".00001"), rounding=ROUND_UP))


def chart_feed(path, stop):
    """Sondea el contrato activo en un hilo independiente del corte de apertura."""
    with closing(sqlite3.connect(path, timeout=10)) as db:
        cached = None
        while not stop.is_set():
            start = int(time.time())//300*300
            try:
                if cached is None or cached["slug"] != slug(start):
                    _, cached, _ = market_for(start)
                _, top, seen, ts = orderbook(cached, "Up")
                bid, ask = number(top["best_bid"]), number(top["best_ask"])
                if ask-bid > number("0.10"):
                    trade = get_json(CLOB, "/last-trade-price", {"token_id": cached["outcome_ids"]["Up"]})
                    value = number(trade["price"])
                    if not trade.get("side") or not 0 < value < 1:
                        raise DataError("Última operación no disponible")
                else:
                    value = (bid+ask)/2
                seen = time.time()
                if start <= ts and seen < start+300:
                    with db:
                        db.execute("INSERT INTO chart_prices VALUES(?,?,?)", (start,seen,float(value)))
                    service_status.record(db, "polymarket", "ok", "Cotización pública recibida", success=True)
            except API_ERRORS as exc:
                service_status.record(db, "polymarket", "error", f"Lectura pendiente · {exc}")
            stop.wait(min(2, 300-time.time()%300))


class Agent:
    def __init__(self, path):
        self.ledger = PaperLedger(path, "100")
        self.db = self.ledger.db
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS chart_prices(start INTEGER,seen REAL,price REAL,PRIMARY KEY(start,seen));
            CREATE TABLE IF NOT EXISTS laya_predictions(start INTEGER PRIMARY KEY,ready REAL,p REAL,detail TEXT);
            CREATE TABLE IF NOT EXISTS laya_status(id INTEGER PRIMARY KEY,detail TEXT);
            CREATE TABLE IF NOT EXISTS laya_sources(start INTEGER PRIMARY KEY,model TEXT);
            CREATE TABLE IF NOT EXISTS ticks(start INTEGER, seen REAL, ts REAL, bid REAL, ask REAL,
                bid_size REAL, ask_size REAL, PRIMARY KEY(start,seen));
            CREATE TABLE IF NOT EXISTS forecasts(start INTEGER PRIMARY KEY, x TEXT, seen REAL,
                p REAL, version TEXT, eligible INTEGER, decision TEXT, winner TEXT, labeled REAL,
                checked REAL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS models(n INTEGER PRIMARY KEY, ready REAL, weights TEXT, report TEXT);
            CREATE TABLE IF NOT EXISTS trace(id INTEGER PRIMARY KEY, start INTEGER, at REAL,
                source TEXT, target TEXT, message TEXT, payload TEXT);
            CREATE INDEX IF NOT EXISTS trace_by_window ON trace(start,id);
            CREATE TABLE IF NOT EXISTS heartbeat(id INTEGER PRIMARY KEY CHECK(id=1), at REAL, detail TEXT);
        """)
        local_observer.tables(self.db)
        service_status.initialize(self.db)
        yahoo_predictor.tables(self.db)
        self.next_view = None
        self.last_maintenance = 0

    def emit(self, start, source, target, message, **data):
        self.db.execute("INSERT INTO trace(start,at,source,target,message,payload) VALUES(?,?,?,?,?,?)",
                        (start, time.time(), source, target, message, dump(data)))

    def observe(self, start):
        if self.next_view is None or self.next_view["slug"] != slug(start):
            _, self.next_view, _ = market_for(start)
        must_be_open(self.next_view)
        book, top, seen, ts = orderbook(self.next_view, "Up")
        if max(seen, ts) > start:
            return
        bid, ask = top["best_bid"], top["best_ask"]
        sizes = [sum(float(t["size"]) for t in book[side] if float(t["price"]) == price)
                 for side, price in (("bids", bid), ("asks", ask))]
        with self.db:
            first = not self.db.execute("SELECT 1 FROM ticks WHERE start=? LIMIT 1", (start,)).fetchone()
            self.db.execute("INSERT OR IGNORE INTO ticks VALUES(?,?,?,?,?,?,?)",
                            (start, seen, ts, bid, ask, *sizes))
            if first:
                self.emit(start, "CLOB", "Libro", "Primera cotización previa a la apertura",
                          bid_up=bid, ask_up=ask, recibido=seen)

    def decide(self, start):
        yahoo_predictor.forecast(self.db,start)
        row = self.db.execute("SELECT decision FROM forecasts WHERE start=?", (start,)).fetchone()
        if row and row[0] is not None:
            return
        if not row:
            model = self.db.execute("SELECT n,ready,weights,report FROM models WHERE ready<=? ORDER BY n DESC LIMIT 1",
                                    (start,)).fetchone()
            with self.db:
                try:
                    if time.time()-start > LIMITS.entry_deadline_seconds:
                        raise ValueError("Apertura perdida")
                    ticks = self.db.execute("SELECT seen,ts,bid,ask,bid_size,ask_size FROM ticks WHERE start=?", (start,))
                    x, seen = ml.features(ticks, start)
                    learned = bool(model and model[2])
                    prob = ml.probability(json.loads(model[2]), x) if learned else x[0]
                    version = f"MLP-{model[0]}" if learned else "Libro Up"
                    eligible = bool(learned and json.loads(model[3])["eligible"] and start-model[1] <= 7*86400)
                    self.db.execute("INSERT INTO forecasts(start,x,seen,p,version,eligible) VALUES(?,?,?,?,?,?)",
                                    (start, dump(x), seen, prob, version, eligible))
                    self.emit(start, "Libro", "Modelo", "Variables congeladas al abrir", **dict(zip(ml.FEATURES, x)), recibido=seen)
                    self.emit(start, "Modelo", "Riesgo", "Pronóstico de apertura", modelo=version, p_up=prob,
                              direccion="Up" if prob >= .5 else "Down", habilitado_paper=eligible)
                except ValueError as exc:
                    self.db.execute("INSERT INTO forecasts(start,decision) VALUES(?,?)", (start, str(exc)))
                    self.emit(start, "Libro", "Riesgo", str(exc))
                    return
        seen, p, version, eligible = self.db.execute(
            "SELECT seen,p,version,eligible FROM forecasts WHERE start=?", (start,)).fetchone()
        existing = self.db.execute("SELECT cost FROM orders WHERE market_id=?", (slug(start),)).fetchone()
        result = {}
        if existing:
            decision = "Compra simulada"
            result = {"total_cost": existing[0]}
        elif not eligible or p == .5:
            decision = "Sin candidato habilitado para simulación" if not eligible else "Señal neutral"
        else:
            try:
                if time.time() - start > LIMITS.entry_deadline_seconds:
                    raise DataError("Ventana de ejecución vencida")
                _, view, raw = market_for(start)
                must_be_open(view)
                side, prob = ("Up", p) if p >= .5 else ("Down", 1-p)
                book, top, _, ts = orderbook(view, side)
                limit = number(prob) - number(LIMITS.minimum_net_edge)
                with self.db:
                    self.emit(start, "CLOB", "Riesgo", "Libro ejecutable del resultado elegido",
                              lado=side, bid=top["best_bid"], ask=top["best_ask"], hora_libro=ts,
                              precio_limite=str(limit), participaciones=str(SHARES), saldo=str(self.ledger.cash))
                fee = costs(raw, book, limit)
                if ts > time.time():
                    time.sleep(max(0, ts-time.time()))
                proposal = dict(now=time.time(), start=start, end=start+300, book_time=ts,
                                inputs_available_at=seen, shares=str(SHARES), p_win=prob, max_price=str(limit),
                                total_costs_extra=fee, min_order_size=book["min_order_size"], model_version=version,
                                market_id=slug(start), outcome=side, best_bid=top["best_bid"],
                                asks=[[v["price"], v["size"]] for v in book["asks"]],
                                model_approved=True, costs_verified=True, rules_verified=True, market_open=True)
                result = self.ledger.buy(proposal, LIMITS)
                decision = result.get("reason", "Compra simulada")
            except API_ERRORS as exc:
                decision = str(exc)
        with self.db:
            self.db.execute("UPDATE forecasts SET decision=? WHERE start=?", (decision, start))
            self.emit(start, "Riesgo", "Cartera", decision, **result)

    def resolve(self, now):
        # Reintentos rotativos para que un mercado atrasado no bloquee los siguientes.
        pending = self.db.execute("""SELECT start FROM forecasts WHERE (p IS NOT NULL OR EXISTS(
            SELECT 1 FROM yahoo_predictions y WHERE y.start=forecasts.start AND y.p IS NOT NULL)) AND winner IS NULL
            AND start+300<=? AND checked<=? ORDER BY checked,start LIMIT 3""", (now, now-60)).fetchall()
        for (start,) in pending:
            with self.db:
                self.db.execute("UPDATE forecasts SET checked=? WHERE start=?", (now, start))
            try:
                event, _, _ = market_for(start)
                seen = time.time()
                record = normalize_resolved(event, iso(seen))
            except API_ERRORS as exc:
                if str(exc) != "Mercado sin resolución final confirmada por Gamma":
                    with self.db:
                        self.emit(start, "Gamma", "Resolución", str(exc))
                continue
            winner = record["outcome"]
            order = self.db.execute("SELECT settled_at FROM orders WHERE market_id=?", (slug(start),)).fetchone()
            if order and order[0] is None:
                self.ledger.settle(slug(start), winner, seen, time.time(), final_verified=True)
            with self.db:
                self.db.execute("UPDATE forecasts SET winner=?, labeled=? WHERE start=?", (winner, seen, start))
                self.emit(start, "Gamma", "Entrenamiento", "Resultado oficial verificado", ganador=winner)
                if order:
                    pnl = self.db.execute("SELECT pnl FROM orders WHERE market_id=?", (slug(start),)).fetchone()[0]
                    self.emit(start, "Resolución", "Cartera", "Pago simulado contabilizado", pnl_neto=pnl)

    def train(self, start):
        n = self.db.execute("SELECT count(*) FROM forecasts WHERE labeled IS NOT NULL AND x IS NOT NULL").fetchone()[0]
        last = self.db.execute("SELECT coalesce(max(n),0) FROM models").fetchone()[0]
        if n < ml.MIN_ROWS or n-last < ml.BATCH:
            return
        rows = self.db.execute("""SELECT start,x,winner,labeled FROM forecasts WHERE labeled IS NOT NULL AND x IS NOT NULL
            ORDER BY start DESC LIMIT ?""", (ml.MAX_ROWS,)).fetchall()
        model, report = ml.fit_candidate([(t, json.loads(x), int(w == "Up"), seen) for t, x, w, seen in rows])
        with self.db:
            self.db.execute("INSERT INTO models VALUES(?,?,?,?)",
                            (n, time.time(), dump(model) if model else None, dump(report)))
            self.emit(start, "Entrenamiento", "Modelo", report["reason"], muestra=n, **report)

    def step(self):
        now = time.time()
        start = int(now)//300*300
        self.decide(start)
        if 45 < now-start < 165 and now-self.last_maintenance >= 20:
            self.last_maintenance = now
            self.resolve(now)
            self.train(start)
        if time.time() < start+300:
            self.observe(start+300)
        return start

def snapshot(path, selected=None):
    with closing(sqlite3.connect(path, timeout=3)) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        rows = lambda sql, args=(): [dict(r) for r in db.execute(sql, args)]
        windows = [r[0] for r in db.execute("""SELECT start FROM forecasts UNION SELECT start FROM ticks
            ORDER BY start DESC LIMIT 144""")]
        current = int(time.time())//300*300
        focus = selected if selected is not None else current
        forecast = rows("SELECT start,seen,p,version,eligible,decision,winner,labeled FROM forecasts WHERE start=?", (focus,))
        ticks = rows("SELECT seen,ts,bid,ask FROM ticks WHERE start=? ORDER BY seen", (focus,))
        trace = rows("SELECT id,at,source,target,message,payload FROM trace WHERE start=? ORDER BY id", (focus,))
        for msg in trace:
            msg["payload"] = json.loads(msg["payload"])
        summary = rows("""SELECT count(p) AS predictions,count(CASE WHEN p IS NOT NULL THEN winner END) AS labeled,
            avg(CASE WHEN winner IS NOT NULL THEN ((p>=0.5)=(winner='Up')) END) AS accuracy,
            avg(CASE WHEN winner IS NOT NULL THEN (((json_extract(x,'$[0]')>=0.5)=(winner='Up'))) END) AS baseline
            FROM forecasts""")[0]
        summary.update(rows("""SELECT count(*) AS orders,count(pnl) AS settled,
            coalesce(sum(CAST(pnl AS REAL)),0) AS pnl FROM orders""")[0])
        summary.update(rows("""SELECT count(*) AS ml_n, avg((p>=0.5)=(winner='Up')) AS ml_accuracy,
            avg((json_extract(x,'$[0]')>=0.5)=(winner='Up')) AS ml_baseline
            FROM forecasts WHERE winner IS NOT NULL AND version LIKE 'MLP-%'""")[0])
        summary["cash"] = float(db.execute("SELECT cash FROM account WHERE id=1").fetchone()[0])
        models = rows("SELECT n,ready,report FROM models ORDER BY n DESC LIMIT 1")
        if models:
            models[0]["report"] = json.loads(models[0]["report"])
        hb = rows("SELECT at,detail FROM heartbeat")
        next_tick = rows("SELECT start,seen,bid,ask FROM ticks ORDER BY start DESC,seen DESC LIMIT 1")
        observer = rows("SELECT model,at,detail FROM observer_status")
        notes = rows("""SELECT at,model,level,note FROM local_notes WHERE start=? AND revision=?
            ORDER BY through_id DESC LIMIT 5""", (focus, local_observer.REVISION))
        prices = rows("SELECT seen,price FROM chart_prices WHERE start=? ORDER BY seen", (focus,))
        prediction = rows("""SELECT ready,p,detail,coalesce(model,'Laya local') AS source
            FROM laya_predictions LEFT JOIN laya_sources USING(start) WHERE start=?""", (focus,))
        comparison = rows("""SELECT count(*) AS n, avg((l.p>=0.5)=(f.winner='Up')) AS laya,
            avg((json_extract(f.x,'$[0]')>=0.5)=(f.winner='Up')) AS book,
            avg(CASE WHEN f.version LIKE 'MLP-%' THEN ((f.p>=0.5)=(f.winner='Up')) END) AS mlp,
            count(CASE WHEN f.version LIKE 'MLP-%' THEN 1 END) AS mlp_n,
            avg(CASE WHEN f.version LIKE 'MLP-%' THEN ((l.p>=0.5)=(f.winner='Up')) END) AS laya_shared
            FROM laya_predictions l JOIN forecasts f USING(start) WHERE l.p IS NOT NULL AND f.winner IS NOT NULL""")[0]
        state = db.execute("SELECT detail FROM laya_status").fetchone()
        yahoo_model = rows("SELECT id,ready,report FROM yahoo_models ORDER BY ready DESC LIMIT 1")
        if yahoo_model:
            yahoo_model[0]["report"] = json.loads(yahoo_model[0]["report"])
        yahoo_forecast = rows("SELECT start,ready,p,model,detail FROM yahoo_predictions WHERE start=?",(focus,))
        yahoo_status = rows("SELECT at,detail FROM yahoo_status WHERE id=1")
        btc = dict(target=next(iter(rows("SELECT seen,price,source FROM btc_targets WHERE start=?",(focus,))),None),
                   quotes=rows("SELECT ts,seen,price,source FROM btc_quotes WHERE ts>=? AND ts<? ORDER BY ts",(focus,focus+300)),
                   forecast=next(iter(rows("SELECT ready,price,anchor,model,detail FROM yahoo_price_predictions WHERE start=?",(focus,))),None),
                   status=next(iter(rows("SELECT at,detail FROM btc_target_status WHERE id=1")),None))
        teacher = rows("SELECT service,state,at,last_ok FROM teacher_connections ORDER BY service")
        yahoo_metrics = rows("""SELECT y.model,count(*) AS n,avg((y.p>=0.5)=(f.winner='Up')) AS accuracy,
            avg((y.p-(f.winner='Up'))*(y.p-(f.winner='Up'))) AS brier,
            avg((json_extract(f.x,'$[0]')>=0.5)=(f.winner='Up')) AS book_accuracy
            FROM yahoo_predictions y JOIN forecasts f USING(start)
            WHERE y.p IS NOT NULL AND f.winner IS NOT NULL AND f.x IS NOT NULL GROUP BY y.model""")
        services = rows("""SELECT service,state,at,last_ok,detail FROM service_status
            ORDER BY CASE service WHEN 'polymarket' THEN 0 WHEN 'laya' THEN 1 ELSE 2 END""")
        by_source = rows("""SELECT coalesce(s.model,'Laya local') AS source, count(*) AS n,
            avg((l.p>=0.5)=(f.winner='Up')) AS accuracy,
            avg((json_extract(f.x,'$[0]')>=0.5)=(f.winner='Up')) AS book_accuracy,
            avg((l.p-(f.winner='Up'))*(l.p-(f.winner='Up'))) AS brier,
            avg((json_extract(f.x,'$[0]')-(f.winner='Up'))*
                (json_extract(f.x,'$[0]')-(f.winner='Up'))) AS book_brier,
            sum(l.ready>l.start+15) AS delayed
            FROM laya_predictions l JOIN forecasts f USING(start)
            LEFT JOIN laya_sources s USING(start)
            WHERE l.p IS NOT NULL AND f.winner IS NOT NULL
            GROUP BY coalesce(s.model,'Laya local') ORDER BY source""")
        return dict(current=current, focus=focus, windows=windows, forecast=next(iter(forecast), None),
                    yahoo=dict(model=next(iter(yahoo_model),None),forecast=next(iter(yahoo_forecast),None),
                               status=next(iter(yahoo_status),None),metrics=yahoo_metrics),
                    services=services,teacher=teacher,btc=btc,comparison_by_source=by_source,mode="paper",
                    prices=prices,laya=next(iter(prediction),None),comparison=comparison,
                    laya_status=state[0] if state else "Esperando inicio",
                    observer=next(iter(observer), None), notes=notes,
                    ticks=ticks, trace=trace, summary=summary, heartbeat=next(iter(hb), None),
                    model=next(iter(models), None), last_tick=next(iter(next_tick), None),
                    min_labels=ml.MIN_ROWS, batch=ml.BATCH)


class Panel(BaseHTTPRequestHandler):
    def __init__(self, *args, db_path, **kwargs):
        self.db_path = db_path
        super().__init__(*args, **kwargs)

    def log_message(self, *args):
        pass

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/":
            payload = Path(__file__).with_name("monitor.html").read_bytes()
            mime = "text/html; charset=utf-8"
        elif url.path == "/api/state":
            try:
                query = parse_qs(url.query).get("start")
                data = snapshot(self.db_path, int(query[0]) if query else None)
                payload = dump(data).encode()
                mime = "application/json; charset=utf-8"
            except (ValueError, OverflowError):
                return self.send_error(400)
        else:
            return self.send_error(404)
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, default=Path("data/live_paper.sqlite"))
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--modelo-local", default=None,
                   help="Usar explícitamente un supervisor Ollama en lugar del remoto")
    p.add_argument("--laya-local", action="store_true", help="Cargar los pesos locales de Laya en CPU")
    p.add_argument("--verificar-conexion", action="store_true",
                   help="Probar ambas rutas remotas con datos de ejemplo sin iniciar la cartera")
    args = p.parse_args()
    config = load_settings()
    if args.verificar_conexion:
        raise SystemExit(check_remote(config))
    args.db.parent.mkdir(parents=True, exist_ok=True)
    with writer_lock(args.db):
        serve(args, config)


def check_remote(config):
    ok = True
    for name, client in (("Laya",RemoteLaya),("Supervisor",RemoteSupervisor)):
        try:
            connection = client.from_env(config)
            if name == "Laya":
                connection.predict([.5,.02,0.,0.,0.])
            else:
                connection.explain({"modo":"prueba","mensajes":[{"ref":"E0","hecho":"Prueba de conexión del monitor."}]})
            print(f"{name}: conexión e inferencia verificadas")
        except RemoteModelError as exc:
            ok = False
            print(f"{name}: {exc}")
    return 0 if ok else 1


def serve(args, config):
    server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(Panel, db_path=args.db))
    agent = Agent(args.db)
    Thread(target=server.serve_forever, daemon=True).start()
    stop = Event()
    if args.modelo_local:
        Thread(target=local_observer.observe, args=(args.db, args.modelo_local, stop), daemon=True).start()
    else:
        try:
            supervisor = RemoteSupervisor.from_env(config)
            Thread(target=local_observer.observe, args=(args.db, "Remoto · "+supervisor.name, stop, supervisor),
                   daemon=True).start()
        except RemoteModelError as exc:
            local_observer.status(agent.db, "Supervisor remoto", str(exc))
            service_status.record(agent.db, "supervisor", "unconfigured", str(exc))
    Thread(target=chart_feed, args=(args.db,stop), daemon=True).start()
    Thread(target=yahoo_predictor.collect,args=(args.db,stop),daemon=True).start()
    Thread(target=btc_reference.collect,args=(args.db,stop),daemon=True).start()
    try:
        predictor = None if args.laya_local else RemoteLaya.from_env(config)
        Thread(target=laya_shadow.run, args=(args.db,"models/laya-en",stop,predictor), daemon=True).start()
    except RemoteModelError as exc:
        with agent.db:
            agent.db.execute("INSERT OR REPLACE INTO laya_status VALUES(1,?)", (str(exc),))
        service_status.record(agent.db, "laya", "unconfigured", str(exc))
    print(f"Monitor Paper en http://127.0.0.1:{server.server_port}  |  Ctrl+C para detener", flush=True)
    try:
        while True:
            detail = "En observación"
            try:
                agent.step()
            except API_ERRORS as exc:
                detail = f"Lectura pendiente · {exc}"
            with agent.db:
                previous = agent.db.execute("SELECT detail FROM heartbeat").fetchone()
                if previous and detail != previous[0] and detail != "En observación":
                    agent.emit(int(time.time())//300*300, "Datos", "Monitor", detail)
                agent.db.execute("INSERT OR REPLACE INTO heartbeat VALUES(1,?,?)", (time.time(), detail))
            # Despertar al inicio incluso si la consulta anterior termina cerca del corte.
            remaining = 300 - time.time() % 300
            time.sleep(min(4 if remaining <= 95 else 10, remaining))
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        agent.ledger.close()


if __name__ == "__main__":
    main()
