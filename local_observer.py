"""Observador Ollama. Solo recibe evidencia del simulador y escribe sus informes."""
from contextlib import closing
import json
import sqlite3
import time
from urllib.error import URLError
from urllib.request import build_opener, HTTPRedirectHandler, ProxyHandler, Request

HOST = "http://127.0.0.1:11434"
PREFERRED_MODEL = "qwen3.5:4b"
REVISION = 2
LEVELS = ("observacion", "atencion")
FORMAT = {"type": "object", "properties": {
    "estado": {"type": "string", "enum": list(LEVELS)},
    "destacados": {"type": "array", "items": {"type": "integer"}, "minItems": 1, "maxItems": 3}},
    "required": ["estado", "destacados"], "additionalProperties": False}
INSTRUCTIONS = (
    "Supervisas un simulador BTC. Lee los eventos en orden cronológico. "
    "Selecciona entre uno y tres índices de los hechos más relevantes para quien supervisa el bot. "
    "Prioriza la decisión de compra o abstención y su causa, la resolución oficial o una avería. "
    "Los índices empiezan en cero. Marca atencion si hay errores, datos vencidos o comportamiento anómalo. "
    "El calentamiento normal es observacion. Devuelve únicamente estado y destacados."
)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("El servidor local intentó redirigir la solicitud")


def ollama(route, body=None):
    # Una dirección literal sin DNS, proxies, redirecciones ni servidores alternativos.
    request = Request(HOST + route, headers={"Content-Type": "application/json"},
                      data=None if body is None else json.dumps(body).encode())
    with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=150 if route == "/api/chat" else 6) as response:
        return json.load(response)


def select_model(requested):
    tags = ollama("/api/tags")["models"]
    local = {m["name"] for m in tags if m.get("name") and not m.get("remote_model")
             and not m.get("remote_host") and "cloud" not in m["name"].lower()}
    if requested not in local:
        raise ValueError(f"Modelo no instalado localmente: {requested}")
    info = ollama("/api/show", {"model": requested})
    if (info.get("remote_model") or info.get("remote_host") or
            not info.get("model_info", {}).get("general.architecture") or
            "completion" not in info.get("capabilities", [])):
        raise ValueError("Ollama no confirmó pesos locales con capacidad de generar texto")
    return requested


def explain(model, evidence):
    # Se vuelve a verificar el alias antes de cada inferencia.
    select_model(model)
    body = {"model": model, "stream": False, "think": False, "format": FORMAT,
            "options": {"temperature": 0, "num_ctx": 2048, "num_predict": 90, "num_thread": 3},
            "messages": [{"role": "system", "content": INSTRUCTIONS},
                         {"role": "user", "content": json.dumps(evidence, ensure_ascii=False)}]}
    response = ollama("/api/chat", body)
    if response.get("done") is not True or response.get("done_reason") == "length":
        raise ValueError("Informe local incompleto")
    note = json.loads(response["message"]["content"])
    if not isinstance(note, dict) or note.get("estado") not in LEVELS:
        raise ValueError("Estado de monitor inválido")
    indices = note.get("destacados")
    events = evidence["mensajes"]
    if (not isinstance(indices, list) or not 1 <= len(indices) <= 3 or
            any(type(i) is not int or not 0 <= i < len(events) for i in indices)):
        raise ValueError("Ollama seleccionó evidencia inexistente")
    return note["estado"], " ".join(events[i]["hecho"] for i in sorted(set(indices)))


def event_fact(source, target, message, data):
    if message == "Pronóstico de apertura":
        return f"{data['modelo']} estimó Up en {data['p_up']:.1%}. La dirección fue {data['direccion']}."
    if message == "Resultado oficial verificado":
        return f"Gamma confirmó el resultado {data['ganador']}."
    if message == "Pago simulado contabilizado":
        return f"La cartera registró {data['pnl_neto']} USD virtuales de resultado neto."
    return f"{source} → {target} · {message}."


def tables(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS local_notes(
            start INTEGER, through_id INTEGER PRIMARY KEY, at REAL, model TEXT, level TEXT, note TEXT,
            revision INTEGER DEFAULT 1);
        CREATE INDEX IF NOT EXISTS notes_by_start ON local_notes(start,through_id);
        CREATE TABLE IF NOT EXISTS observer_status(
            id INTEGER PRIMARY KEY CHECK(id=1), at REAL, model TEXT, detail TEXT);
    """)
    if "revision" not in {row[1] for row in db.execute("PRAGMA table_info(local_notes)")}:
        db.execute("ALTER TABLE local_notes ADD COLUMN revision INTEGER DEFAULT 1")


def status(db, model, detail):
    with db:
        db.execute("INSERT OR REPLACE INTO observer_status VALUES(1,?,?,?)", (time.time(), model, detail))


def next_evidence(db, now):
    # Agrupa mensajes del mismo intervalo. Prioriza la actividad más reciente tras una pausa.
    row = db.execute("""SELECT start,max(id) AS last FROM trace
        WHERE at >= ? GROUP BY start
        HAVING max(id)>coalesce((SELECT max(through_id) FROM local_notes n WHERE n.start=trace.start AND revision=?),0)
        ORDER BY last DESC LIMIT 1""", (now-3600, REVISION)).fetchone()
    if not row:
        return None
    start, through = row
    messages = db.execute("""SELECT source,target,message,payload FROM trace
        WHERE start=? AND id<=? ORDER BY id DESC LIMIT 8""", (start, through)).fetchall()
    forecast = db.execute("SELECT p,version,decision,winner FROM forecasts WHERE start=?", (start,)).fetchone()
    evidence = {"mercado": f"btc-updown-5m-{start}", "modo": "simulación",
                "mensajes": [{"indice": i, "hecho": event_fact(src, dst, msg, json.loads(data))}
                             for i, (src, dst, msg, data) in enumerate(reversed(messages))]}
    if forecast:
        evidence.update(dict(zip(("p_up", "fuente_pronostico", "decision", "resultado_oficial"), forecast)))
    return start, through, evidence


def observe(path, model, stop):
    with closing(sqlite3.connect(path, timeout=10)) as db:
        status(db, model, "Esperando actividad")
        delay = 0
        while not stop.wait(delay):
            try:
                pending = next_evidence(db, time.time())
                if not pending:
                    delay = 4
                    continue
                start, through, evidence = pending
                status(db, model, "Analizando intervalo")
                level, summary = explain(model, evidence)
                with db:
                    db.execute("INSERT OR REPLACE INTO local_notes VALUES(?,?,?,?,?,?,?)",
                               (start, through, time.time(), model, level, summary, REVISION))
                status(db, model, "Seguimiento activo")
                delay = 10
            except (URLError, OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
                status(db, model, f"Reintentará en 30 s · {exc}")
                delay = 30
