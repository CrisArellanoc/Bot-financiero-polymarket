"""Predictor experimental remoto o local que evalúa las entradas de apertura."""
import json
import math
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from live_model import FEATURES

REVISION = "fd4f9dbcf4fde222fd43174bdd4c3b08b789e4e1"
QUESTION = {"up": {"type": "noul", "instructions":
    "Will Bitcoin's final resolution reference price be greater than or equal to its opening price in this five-minute interval?"}}


def load_model(directory):
    if not (Path(directory) / "model.safetensors").is_file():
        raise FileNotFoundError("Faltan los pesos locales; ejecuta python laya_shadow.py para prepararlos")
    import torch
    import laya
    torch.set_num_threads(1)
    return laya.load(str(Path(directory).resolve()), device="cpu", backend="eager")


def make_state(values):
    return {"document": "Pre-opening Up contract order book. Mid and spread in dollars per share; "
             "imbalance positive means greater bid size; momentum and volatility of mid over the last 90 seconds. "
             + json.dumps(dict(zip(FEATURES, (round(v, 5) for v in values), strict=True)))}


def predict(model, values):
    result = float(model.predict(make_state(values), QUESTION)["answers"]["up"]["noul"])
    if not math.isfinite(result) or not 0 <= result <= 1:
        raise ValueError("Puntuación de Laya fuera de rango")
    return result


def run(path, directory, stop, remote=None):
    with closing(sqlite3.connect(path, timeout=10)) as db:
        def status(detail):
            with db:
                db.execute("INSERT OR REPLACE INTO laya_status VALUES(1,?)", (detail,))
        try:
            if remote is None:
                status("Cargando pesos en CPU")
                model = load_model(directory)
                infer = lambda values: predict(model, values)
                source = "Laya local"
            else:
                infer = remote.predict
                source = "Remoto · " + remote.name
        except Exception as exc:
            status(str(exc))
            return
        status(source + " · esperando apertura")
        while not stop.is_set():
            now = time.time()
            start = int(now)//300*300
            if now-start < 15:
                row = db.execute("""SELECT x FROM forecasts WHERE start=? AND x IS NOT NULL
                    AND NOT EXISTS(SELECT 1 FROM laya_predictions WHERE start=?)""", (start,start)).fetchone()
                if row:
                    score = None
                    try:
                        score = infer(json.loads(row[0]))
                        detail = "Disponible"
                    except Exception as exc:
                        detail = str(exc)
                    ready = time.time()
                    if ready >= start+300:
                        score, detail = None, "Inferencia terminada después del cierre"
                    with db:
                        db.execute("INSERT OR IGNORE INTO laya_predictions VALUES(?,?,?,?)",
                                   (start,ready,score,detail))
                        db.execute("INSERT OR REPLACE INTO laya_sources VALUES(?,?)", (start,source))
                        evidence = {"modelo":source, "p_up":score, "hora_resultado":ready}
                        message = "Predicción experimental de Laya" if score is not None else detail
                        db.execute("INSERT INTO trace(start,at,source,target,message,payload) VALUES(?,?,?,?,?,?)",
                                   (start,ready,"Laya","Supervisor",message,json.dumps(evidence)))
                    status(source + " · " + detail)
            remaining = 300-time.time()%300
            stop.wait(min(0.4 if now-start < 15 else 10, remaining))


if __name__ == "__main__":
    from huggingface_hub import snapshot_download
    snapshot_download("convaiinnovations/laya", revision=REVISION, local_dir="models/laya-en",
                      allow_patterns=["model.safetensors","rl_agent_config.json","encoder/config.json","tokenizer/*"])
