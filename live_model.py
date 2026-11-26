"""MLP de apertura. Cada fila procede de cotizaciones observadas antes del corte."""
import math
import statistics

MIN_ROWS = 288
BATCH = 48
MAX_ROWS = 2016
FEATURES = ("mid_up", "spread_up", "imbalance", "momentum", "volatility")
MAX_INPUT_AGE = 24
CLOCK_TOLERANCE = 3  # Segundos; ambos relojes deben quedar antes del corte.


def features(ticks, start):
    # (received_at, book_time, bid, ask, best_bid_size, best_ask_size)
    window = sorted(t for t in ticks if start - 90 <= t[0] <= start and t[1] <= start
                    and -CLOCK_TOLERANCE <= t[0] - t[1] <= MAX_INPUT_AGE)
    if len(window) < 2 or start - window[-1][1] > MAX_INPUT_AGE:
        raise ValueError("Faltan dos cotizaciones recientes observadas antes de abrir")
    if window[-1][0] - window[0][0] < 5:
        raise ValueError("Se necesitan al menos cinco segundos de observación")
    mids = [(t[2] + t[3]) / 2 for t in window]
    _, _, bid, ask, bid_size, ask_size = window[-1]
    return [mids[-1], ask - bid, (bid_size - ask_size) / (bid_size + ask_size),
            mids[-1] - mids[0], statistics.pstdev(mids)], max(max(t[:2]) for t in window)


def sigmoid(x):
    return 1 / (1 + math.exp(-max(-500, min(500, x))))


def score(model, x):
    scaled = [(v - m) / s for v, m, s in zip(x, model["mean"], model["scale"], strict=True)]
    if "layers" in model:
        layer = scaled
        for i, params in enumerate(model["layers"]):
            values = [b+sum(v*w[j] for v,w in zip(layer,params["weights"],strict=True))
                      for j,b in enumerate(params["bias"])]
            layer = [math.tanh(v) for v in values] if i < len(model["layers"])-1 else values
        return layer[0]
    hidden = [math.tanh(b + sum(v * w[j] for v, w in zip(scaled, model["w1"])))
              for j, b in enumerate(model["b1"])]
    return model["b2"] + sum(v*w for v, w in zip(hidden, model["w2"], strict=True))


def probability(model, x):
    return sigmoid(model["a"] * score(model, x) + model["b"])


def fit_candidate(rows):
    """Filas (apertura, X, y_up, primera observación oficial), ordenadas por apertura."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.neural_network import MLPClassifier
    from sklearn.preprocessing import StandardScaler
    from threadpoolctl import threadpool_limits

    rows = sorted(rows, key=lambda r: r[0])[-MAX_ROWS:]
    if len(rows) < MIN_ROWS:
        raise ValueError("Muestra prospectiva insuficiente")
    a, b = int(len(rows) * .55), int(len(rows) * .75)
    train = [r for r in rows[:a] if r[3] <= rows[a][0]]
    cal = [r for r in rows[a:b] if r[3] <= rows[b][0]]
    valid = rows[b:]
    report = {"train": len(train), "calibration": len(cal), "validation": len(valid)}
    if any({r[2] for r in part} != {0, 1} for part in (train, cal)):
        return None, {**report, "eligible": False, "reason": "Faltan ambas clases tras purgar etiquetas tardías"}
    xs, ys = [r[1] for r in train], [r[2] for r in train]
    scaler = StandardScaler().fit(xs)
    with threadpool_limits(limits=1):
        net = MLPClassifier(hidden_layer_sizes=(16,8), activation="tanh", solver="lbfgs",
                            alpha=1, max_iter=400, random_state=123).fit(scaler.transform(xs), ys)
    model = {"mean": scaler.mean_.tolist(), "scale": scaler.scale_.tolist(),
             "layers":[{"weights":w.tolist(),"bias":bias.tolist()}
                       for w,bias in zip(net.coefs_,net.intercepts_,strict=True)]}
    report.update(architecture=[5,16,8,1],iterations=int(net.n_iter_),
                  purpose="prospective paper candidate; profitability not established")
    calibrator = LogisticRegression(random_state=123).fit(
        [[score(model, r[1])] for r in cal], [r[2] for r in cal])
    model.update(a=float(calibrator.coef_[0, 0]), b=float(calibrator.intercept_[0]))
    blocks = []
    for block in (valid[:len(valid)//2], valid[len(valid)//2:]):
        pairs = [(probability(model, x), x[0], y) for _, x, y, _ in block]
        blocks.append({"n": len(block),
                       "accuracy": statistics.mean((p >= .5) == y for p, _, y in pairs),
                       "book_accuracy": statistics.mean((m >= .5) == y for _, m, y in pairs),
                       "brier": statistics.mean((p-y)**2 for p, _, y in pairs),
                       "book_brier": statistics.mean((m-y)**2 for _, m, y in pairs)})
    eligible = all(v["accuracy"] > v["book_accuracy"] and v["brier"] < v["book_brier"] for v in blocks)
    return model, {**report, "blocks": blocks, "eligible": eligible,
                   "reason": ("Supera al libro en acierto y Brier en ambos bloques" if eligible
                              else "Todavía no supera al libro en ambos bloques")}
