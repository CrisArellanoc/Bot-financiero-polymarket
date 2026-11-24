"""Observador Ollama. Solo recibe evidencia del simulador y escribe sus informes."""
from contextlib import closing
import json
import sqlite3
import time
import service_status
from urllib.error import URLError
from urllib.request import build_opener, HTTPRedirectHandler, ProxyHandler, Request

HOST = "http://127.0.0.1:11434"
PREFERRED_MODEL = "qwen3.5:4b"
REVISION = 2
LEVELS = ("observacion", "atencion")
INSTRUCTIONS = (
    "Supervisas un simulador BTC. Lee los eventos en orden cronológico. "
    "Selecciona el evento principal y, si aporta contexto, un evento secundario. "
    "Prioriza la decisión de compra o abstención y su causa, la resolución oficial o una avería. "
    "Usa las referencias exactas E0, E1, etc. Elige ninguno si no hay un segundo evento. "
    "Marca atencion si hay errores, datos vencidos o comportamiento anómalo. "
    "El calentamiento normal es observacion. Devuelve estado, principal y secundario."
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
    events = evidence["mensajes"]
    refs = {f"E{i}": event["hecho"] for i, event in enumerate(events)}
    schema = {"type": "object", "properties": {
        "estado": {"type": "string", "enum": list(LEVELS)},
        "principal": {"type": "string", "enum": list(refs)},
        "secundario": {"type": "string", "enum": ["ninguno", *refs]}},
        "required": ["estado", "principal", "secundario"], "additionalProperties": False}
    body = {"model": model, "stream": False, "think": False, "format": schema,
            "options": {"temperature": 0, "num_ctx": 2048, "num_predict": 90, "num_thread": 3},
            "messages": [{"role": "system", "content": INSTRUCTIONS},
                         {"role": "user", "content": json.dumps(evidence, ensure_ascii=False)}]}
    response = ollama("/api/chat", body)
    if response.get("done") is not True or response.get("done_reason") == "length":
        raise ValueError("Informe local incompleto")
    note = json.loads(response["message"]["content"])
    return validate_note(note, evidence)


def validate_note(note, evidence):
    refs = {f"E{i}": event["hecho"] for i, event in enumerate(evidence["mensajes"])}
    if not isinstance(note, dict) or note.get("estado") not in LEVELS:
        raise ValueError("Estado de monitor inválido")
    first, second = note.get("principal"), note.get("secundario")
    if not isinstance(first, str) or not isinstance(second, str) or first not in refs or (second != "ninguno" and second not in refs):
        raise ValueError("Ollama seleccionó evidencia inexistente")
    return note["estado"], " ".join(fact for ref, fact in refs.items() if ref in (first, second))


def event_fact(source, target, message, data):
    if source == "Laya" and data.get("p_up") is not None:
        return f"{data['modelo']} registró una puntuación Up de {data['p_up']:.1%}."
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


def status(db, model, detail, state="waiting", success=False):
    with db:
        db.execute("INSERT OR REPLACE INTO observer_status VALUES(1,?,?,?)", (time.time(), model, detail))
    service_status.record(db, "supervisor", state, detail, success)


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
                "mensajes": [{"ref": f"E{i}", "hecho": event_fact(src, dst, msg, json.loads(data))}
                             for i, (src, dst, msg, data) in enumerate(reversed(messages))]}
    if forecast:
        evidence.update(dict(zip(("p_up", "fuente_pronostico", "decision", "resultado_oficial"), forecast)))
    return start, through, evidence


def observe(path, model, stop, remote=None):
    with closing(sqlite3.connect(path, timeout=10)) as db:
        status(db, model, "Esperando actividad")
        delay = 0
        last_check = 0
        while not stop.wait(delay):
            try:
                now = time.time()
                if remote and now-last_check >= 60:
                    health_event(db, now)
                    last_check = now
                pending = next_evidence(db, now)
                if not pending:
                    delay = 4
                    continue
                start, through, evidence = pending
                status(db, model, "Analizando intervalo", "busy")
                if remote:
                    level, summary = remote.explain(evidence)
                    model = "Remoto · " + remote.name
                else:
                    level, summary = explain(model, evidence)
                with db:
                    db.execute("INSERT OR REPLACE INTO local_notes VALUES(?,?,?,?,?,?,?)",
                               (start, through, time.time(), model, level, summary, REVISION))
                status(db, model, "Seguimiento activo", "ok", success=True)
                delay = 10
            except (URLError, OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
                status(db, model, f"Reintentará en 30 s · {exc}", "error")
                delay = 30


def health_event(db, now):
    start = int(now)//300*300
    tick = db.execute("SELECT max(seen) FROM chart_prices WHERE start=?", (start,)).fetchone()[0]
    beat = db.execute("SELECT at FROM heartbeat WHERE id=1").fetchone()
    freshness = "sin cotización" if tick is None else f"última cotización hace {max(0,int(now-tick))} s"
    activity = "iniciando" if not beat else f"última actividad del bot hace {max(0,int(now-beat[0]))} s"
    problems = db.execute("SELECT service,detail FROM service_status WHERE state IN ('error','unconfigured')").fetchall()
    details = " · ".join(f"{name} · {detail}" for name, detail in problems)
    with db:
        db.execute("INSERT INTO trace(start,at,source,target,message,payload) VALUES(?,?,?,?,?,?)",
                   (start,now,"Monitor","Supervisor",f"Control periódico · {freshness}, {activity}" +
                    (" · " + details if details else ""), "{}"))
