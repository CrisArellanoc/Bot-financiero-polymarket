"""Memoria prospectiva para el chat remoto, usando únicamente aperturas ya resueltas."""
import hashlib
import json
import math

from live_model import FEATURES

MIN_HISTORY = 24
MAX_HISTORY = 256
NEIGHBORS = 5


def historical_examples(db, start):
    rows = db.execute("""SELECT f.start,f.x,f.winner FROM forecasts f JOIN brain_decisions b USING(start)
        WHERE f.seen<=f.start AND f.start+300<=f.labeled AND f.labeled<=?
          AND f.winner IN ('Up','Down') AND f.x IS NOT NULL
        ORDER BY f.start DESC LIMIT ?""",(start,MAX_HISTORY))
    examples = []
    for window, encoded, winner in rows:
        try:
            x = json.loads(encoded)
            if not isinstance(x,list) or len(x)!=len(FEATURES) or not all(type(v) in (int,float) and math.isfinite(v) for v in x):
                continue
        except (ValueError,TypeError):
            continue
        examples.append((window,x,winner))
    return examples


def add_context(db, start, x, evidence):
    examples = historical_examples(db,start)
    if len(examples)<MIN_HISTORY:
        return evidence["prompt_version"]
    # Bloques de dos aperturas: orden determinista independiente del resultado.
    slot = start//300
    flip = hashlib.sha256(f"memoria-v1/{slot//2}".encode()).digest()[0] % 2
    assisted = slot%2 == flip
    arm = "memoria" if assisted else "control"
    version = evidence["prompt_version"] + "/" + arm + "-v1"
    evidence["prompt_version"] = version
    if assisted:
        # Rangos de escala típicos del libro, con mínimo para columnas constantes.
        columns = [sorted(column) for column in zip(*(ex[1] for ex in examples))]
        n = len(examples)
        floors = (.01,.01,.1,.01,.005)
        scales = [max(floor,col[3*n//4]-col[n//4]) for floor,col in zip(floors,columns)]
        def distance(example):
            return sum(abs(a-b)/s for a,b,s in zip(x,example[1],scales))
        neighbors = sorted(examples,key=distance)[:NEIGHBORS]
        evidence["casos_similares"] = [dict(start=t,indicadores=dict(zip(FEATURES,features)),ganador=y)
                                        for t,features,y in neighbors]
        ups = sum(y=="Up" for _,_,y in neighbors)
        evidence["mensajes"].append(dict(ref="E5",hecho=f"De {n} aperturas resueltas, las {NEIGHBORS} más cercanas en indicadores del libro terminaron en Up {ups} veces y en Down {NEIGHBORS-ups} veces. Considera los casos y su dispersión; es una muestra pequeña."))
    return version
