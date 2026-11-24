"""MLP histórico BTC-USD y evaluación prospectiva contra Polymarket."""
import argparse
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import statistics
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from live_model import sigmoid
import btc_reference

LAGS = 24
FEATURES = [f"return_{i}" for i in range(LAGS)] + ["mean_6", "volatility_24", "range_last", "volume_ratio"]
SOURCE = "https://query1.finance.yahoo.com/v8/finance/chart/BTC-USD"
TARGET = "Yahoo BTC-USD 5m close >= open"


def download(period):
    if period not in ("60d", "1d"):
        raise ValueError("Periodo Yahoo inválido")
    request = Request(SOURCE + f"?range={period}&interval=5m",
                      headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=20) as response:
                return json.load(response)
        except HTTPError as exc:
            code = exc.code
            exc.close()
            if code not in (429,500,502,503,504) or attempt == 2:
                raise ValueError(f"Yahoo respondió HTTP {code}") from None
        except (URLError, TimeoutError):
            if attempt == 2:
                raise ValueError("Yahoo no respondió. Se conservan los datos y el modelo anteriores") from None
        time.sleep(2**attempt)


def normalize(payload, cutoff):
    try:
        data = payload["chart"]["result"][0]
        if data["meta"]["symbol"] != "BTC-USD":
            raise ValueError("Símbolo Yahoo inesperado")
        times = data["timestamp"]
        values = data["indicators"]["quote"][0]
        if any(len(values[key]) != len(times) for key in ("open","high","low","close","volume")):
            raise ValueError("Longitudes Yahoo inconsistentes")
    except (KeyError,TypeError,IndexError):
        raise ValueError("Yahoo devolvió un historial inválido") from None
    rows = {}
    rejected = 0
    for i, timestamp in enumerate(times):
        try:
            start = float(timestamp)
            prices = [float(values[key][i]) for key in ("open","high","low","close","volume")]
            opening, high, low, close, volume = prices
            valid = (math.isfinite(start) and start % 300 == 0 and start+300 <= cutoff
                     and all(math.isfinite(v) for v in prices)
                     and min(opening,high,low,close) > 0 and volume >= 0
                     and low <= min(opening,close) <= max(opening,close) <= high)
            if not valid:
                raise ValueError()
        except (ValueError,TypeError,OverflowError):
            rejected += 1
            continue
        if int(start) in rows:
            raise ValueError("Yahoo devolvió velas duplicadas")
        rows[int(start)] = dict(start=int(start),open=opening,high=high,low=low,close=close,volume=volume)
    ordered = sorted(rows.values(), key=lambda r:r["start"])
    return ordered, rejected


def features(window, target_start):
    if len(window) != LAGS or window[-1]["start"] != target_start-600:
        raise ValueError("Falta la ventana Yahoo con un intervalo de separación")
    if any(b["start"]-a["start"] != 300 for a,b in zip(window,window[1:])):
        raise ValueError("Hueco en las velas Yahoo")
    returns = [math.log(r["close"]/r["open"]) for r in window]
    last = window[-1]
    average_volume = statistics.mean(r["volume"] for r in window)
    values = returns + [statistics.mean(returns[-6:]),statistics.pstdev(returns),
                      (last["high"]-last["low"])/last["open"],
                      last["volume"]/average_volume if average_volume else 0.]
    if not all(math.isfinite(v) for v in values):
        raise ValueError("Variables Yahoo no finitas")
    return values


def samples(rows):
    result = []
    for i in range(LAGS+1,len(rows)):
        target = rows[i]
        try:
            x = features(rows[i-LAGS-1:i-1],target["start"])
        except ValueError:
            continue
        result.append((target["start"],x,int(target["close"] >= target["open"])))
    return result


def score(weights, x):
    layer = [(v-m)/s for v,m,s in zip(x,weights["mean"],weights["scale"],strict=True)]
    for i, parameters in enumerate(weights["layers"]):
        output = [bias+sum(v*w[j] for v,w in zip(layer,parameters["weights"],strict=True))
                  for j,bias in enumerate(parameters["bias"])]
        layer = [math.tanh(v) for v in output] if i < len(weights["layers"])-1 else output
    return layer[0]


def probability(weights, x):
    return sigmoid(weights["a"]*score(weights,x)+weights["b"])


def measurements(pairs):
    return {"n":len(pairs),"accuracy":statistics.mean((p>=.5)==y for p,y in pairs),
            "brier":statistics.mean((p-y)**2 for p,y in pairs)}


def train(rows):
    from sklearn.linear_model import LogisticRegression
    from sklearn.neural_network import MLPClassifier, MLPRegressor
    from sklearn.preprocessing import StandardScaler
    from threadpoolctl import threadpool_limits

    data = samples(rows)
    if len(data) < 1000:
        raise ValueError("Se necesitan al menos 1000 ventanas válidas para este experimento")
    a,b = int(len(data)*.6),int(len(data)*.8)
    training,calibration,test = data[:a],data[a:b],data[b:]
    if any({r[2] for r in part} != {0,1} for part in (training,calibration)):
        raise ValueError("Faltan las dos clases en entrenamiento o calibración")
    scaler = StandardScaler().fit([r[1] for r in training])
    with threadpool_limits(limits=1):
        net = MLPClassifier(hidden_layer_sizes=(16,8),activation="tanh",solver="adam",
                            alpha=1,max_iter=200,shuffle=False,batch_size=128,random_state=123)
        net.fit(scaler.transform([r[1] for r in training]),[r[2] for r in training])
        weights = {"mean":scaler.mean_.tolist(),"scale":scaler.scale_.tolist(),
                   "layers":[{"weights":w.tolist(),"bias":bias.tolist()}
                             for w,bias in zip(net.coefs_,net.intercepts_,strict=True)]}
        calibrator = LogisticRegression(random_state=123).fit(
            [[score(weights,r[1])] for r in calibration],[r[2] for r in calibration])
    weights.update(a=float(calibrator.coef_[0,0]),b=float(calibrator.intercept_[0]))
    # Regresión de precio independiente de la probabilidad Up. La base es el
    # último cierre de entrada, conocido 5 minutos antes de abrir el mercado.
    by_time = {r["start"]:r for r in rows}
    returns = lambda part: [math.log(by_time[t]["close"]/by_time[t-600]["close"]) for t,_,_ in part]
    target_scale = statistics.pstdev(returns(training)) or 1.
    with threadpool_limits(limits=1):
        reg = MLPRegressor(hidden_layer_sizes=(16,8),activation="tanh",alpha=3,
                           max_iter=120,shuffle=False,batch_size=128,random_state=123)
        reg.fit(scaler.transform([r[1] for r in training]),[v/target_scale for v in returns(training)])
    price_model = {"mean":weights["mean"],"scale":weights["scale"],"target_scale":target_scale,
                   "layers":[{"weights":w.tolist(),"bias":bias.tolist()}
                             for w,bias in zip(reg.coefs_,reg.intercepts_,strict=True)]}
    weights["price_model"] = price_model
    errors, baseline = [],[]
    for t,x,_ in test:
        anchor,actual = by_time[t-600]["close"],by_time[t]["close"]
        errors.append(price_prediction(price_model,x,anchor)-actual)
        baseline.append(anchor-actual)
    price_metrics = lambda values: dict(mae_usd=statistics.mean(abs(v) for v in values),
                                        rmse_usd=math.sqrt(statistics.mean(v*v for v in values)))
    prior = statistics.mean(r[2] for r in training)
    report = {"target":TARGET,"features":FEATURES,"lookback_bars":LAGS,"gap_bars":1,
              "architecture":[len(FEATURES),16,8,1],"iterations":int(net.n_iter_),
              "train_n":len(training),"calibration_n":len(calibration),
              "test_first":test[0][0],"test_last":test[-1][0],
              "test":measurements([(probability(weights,x),y) for _,x,y in test]),
              "majority":measurements([(prior,y) for _,_,y in test]),
              "persistence":measurements([(float(x[LAGS-1]>=0),y) for _,x,y in test]),
              "price_regression":{"target":"log(target close / last input close)","anchor_lag_seconds":300,
                                  "horizon_from_anchor_seconds":600,"test_n":len(test),
                                  "test":price_metrics(errors),"last_price_baseline":price_metrics(baseline),
                                  "iterations":int(reg.n_iter_),"source":"Yahoo BTC-USD, not Chainlink"},
              "historical_availability":"closed candles with one-bar gap; publication times not proven",
              "real_money_enabled":False,"paper_trading_approved":False}
    return weights,report


def tables(db):
    btc_reference.tables(db)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS yahoo_candles(
            start INTEGER PRIMARY KEY, open REAL, high REAL, low REAL, close REAL, volume REAL, available REAL);
        CREATE TABLE IF NOT EXISTS yahoo_models(
            id TEXT PRIMARY KEY, ready REAL, weights TEXT, report TEXT);
        CREATE TABLE IF NOT EXISTS yahoo_predictions(
            start INTEGER PRIMARY KEY, ready REAL, p REAL, model TEXT, x TEXT, inputs_available REAL, detail TEXT);
        CREATE TABLE IF NOT EXISTS yahoo_status(id INTEGER PRIMARY KEY CHECK(id=1), at REAL, detail TEXT);
    """)


def status(db, detail):
    with db:
        db.execute("INSERT OR REPLACE INTO yahoo_status VALUES(1,?,?)",(time.time(),detail))


def store_candles(db, rows, observed):
    with db:
        db.executemany("""INSERT INTO yahoo_candles VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(start) DO UPDATE SET
                available=CASE WHEN yahoo_candles.open=excluded.open AND yahoo_candles.high=excluded.high
                    AND yahoo_candles.low=excluded.low AND yahoo_candles.close=excluded.close
                    AND yahoo_candles.volume=excluded.volume THEN yahoo_candles.available ELSE excluded.available END,
                open=excluded.open, high=excluded.high, low=excluded.low,
                close=excluded.close, volume=excluded.volume""",
            [(r["start"],r["open"],r["high"],r["low"],r["close"],r["volume"],observed) for r in rows])
        # El historial de entrenamiento queda en la captura. La caché en vivo requiere solo las últimas 48 h.
        db.execute("DELETE FROM yahoo_candles WHERE start<?",(observed-2*86400,))


def forecast(db, start):
    if db.execute("SELECT 1 FROM yahoo_predictions WHERE start=?",(start,)).fetchone():
        return
    model = db.execute("SELECT id,ready,weights FROM yahoo_models ORDER BY ready DESC LIMIT 1").fetchone()
    if not model:
        return
    now = time.time()
    p,x,available = None,None,None
    try:
        if not start <= now <= start+15 or model[1] > start:
            raise ValueError("La apertura ocurrió antes de disponer de los datos o del modelo")
        records = db.execute("""SELECT start,open,high,low,close,volume,available FROM yahoo_candles
            WHERE start>=? AND start<=? ORDER BY start""", (start-(LAGS+1)*300,start-600)).fetchall()
        window = [dict(zip(("start","open","high","low","close","volume","available"),row)) for row in records]
        x = features(window,start)
        available = max(r["available"] for r in window)
        if available > start:
            raise ValueError("Las entradas Yahoo se observaron después de la apertura")
        if start-model[1] > 7*86400:
            raise ValueError("Modelo Yahoo de más de siete días. Repite el entrenamiento")
        weights = json.loads(model[2])
        p = probability(weights,x)
        detail = "Predicción experimental de apertura"
    except ValueError as exc:
        detail = str(exc)
    price,anchor,price_detail = None,None,"Estimación USD no disponible"
    if p is not None and weights.get("price_model"):
        anchor = window[-1]["close"]
        try:
            price = price_prediction(weights["price_model"],x,anchor)
            price_detail = "Estimación experimental del cierre Yahoo BTC-USD"
        except ValueError as exc:
            price_detail = str(exc)
    with db:
        db.execute("INSERT INTO yahoo_predictions VALUES(?,?,?,?,?,?,?)",
                   (start,now,p,model[0],json.dumps(x),available,detail))
        if p is not None and weights.get("price_model"):
            db.execute("INSERT OR IGNORE INTO yahoo_price_predictions VALUES(?,?,?,?,?,?)",
                       (start,now,price,anchor,model[0],price_detail))
        if p is not None:
            db.execute("INSERT INTO trace(start,at,source,target,message,payload) VALUES(?,?,?,?,?,?)",
                       (start,now,"Yahoo","Yahoo MLP","Ventana Yahoo congelada",json.dumps({"entradas_disponibles":available})))
        db.execute("INSERT INTO trace(start,at,source,target,message,payload) VALUES(?,?,?,?,?,?)",
                   (start,now,"Yahoo MLP","Supervisor",detail,
                    json.dumps({"p_up":p,"modelo":model[0],"entradas_disponibles":available},allow_nan=False)))


def price_prediction(weights, x, anchor):
    change = score(weights,x)*weights["target_scale"]
    if not math.isfinite(change) or abs(change) > .1 or not math.isfinite(anchor) or anchor <= 0:
        raise ValueError("Estimación de precio fuera del rango admitido")
    return anchor*math.exp(change)


def store_quote(db, payload, observed):
    try:
        meta = payload["chart"]["result"][0]["meta"]
        ts,price = float(meta["regularMarketTime"]),float(meta["regularMarketPrice"])
        if meta["symbol"] != "BTC-USD" or not all(math.isfinite(v) for v in (ts,price)) or price <= 0:
            raise ValueError()
        if not observed-180 <= ts <= observed+10:
            raise ValueError()
    except (KeyError,TypeError,ValueError,IndexError):
        return False
    with db:
        db.execute("INSERT OR IGNORE INTO btc_quotes VALUES(?,?,?,?)",(ts,observed,price,"Yahoo Finance BTC-USD"))
        db.execute("DELETE FROM btc_quotes WHERE ts<?",(observed-2*86400,))
    return True


def collect(path, stop):
    with closing(sqlite3.connect(path,timeout=10)) as db:
        while not stop.is_set():
            try:
                payload = download("1d")
                now = time.time()
                rows,_ = normalize(payload,now)
                if not rows:
                    raise ValueError("Yahoo no devolvió velas completas")
                store_candles(db,rows,now)
                fresh = store_quote(db,payload,now)
                status(db,"Velas BTC-USD actualizadas" + (" · cotización Yahoo recibida" if fresh else " · cotización actual pendiente"))
            except (ValueError,OSError,KeyError,TypeError) as exc:
                status(db,f"Actualización pendiente · {exc}")
            stop.wait(10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",type=Path,default=Path("data/live_paper.sqlite"))
    args = parser.parse_args()
    args.db.parent.mkdir(parents=True,exist_ok=True)
    payload = download("60d")
    observed = time.time()
    rows,rejected = normalize(payload,observed)
    raw = json.dumps(payload,allow_nan=False,separators=(",",":"))
    digest = hashlib.sha256(raw.encode()).hexdigest()
    weights,report = train(rows)
    snapshot = args.db.parent/"yahoo_history"/(digest+".json")
    snapshot.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w",encoding="utf-8",dir=snapshot.parent,suffix=".tmp",delete=False) as handle:
        handle.write(raw)
    Path(handle.name).replace(snapshot)
    report["snapshot"] = str(snapshot.relative_to(args.db.parent))
    report.update(data_sha256=digest,observed_at=observed,accepted_candles=len(rows),rejected_rows=rejected)
    ready = time.time()
    identity = "yahoo-mlp-"+hashlib.sha256(json.dumps([weights,report],sort_keys=True).encode()).hexdigest()[:16]
    with closing(sqlite3.connect(args.db,timeout=10)) as db:
        tables(db)
        with db:
            db.execute("INSERT INTO yahoo_models VALUES(?,?,?,?)",
                       (identity,ready,json.dumps(weights,allow_nan=False),json.dumps(report,allow_nan=False)))
        store_candles(db,rows,observed)
        status(db,"Modelo histórico preparado para evaluación prospectiva")
    print(json.dumps({"model":identity,**report},indent=2,allow_nan=False))


if __name__ == "__main__":
    main()
