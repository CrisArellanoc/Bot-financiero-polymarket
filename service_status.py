"""Estado reciente de cada conexión, compartido con el panel y el supervisor."""
import time


def record(db, service, state, detail, success=False):
    now = time.time()
    with db:
        db.execute("""INSERT INTO service_status VALUES(?,?,?,?,?)
            ON CONFLICT(service) DO UPDATE SET state=excluded.state,
                at=excluded.at, detail=excluded.detail,
                last_ok=coalesce(excluded.last_ok,service_status.last_ok)""",
                   (service, state, now, now if success else None, detail))


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS teacher_connections(
        service TEXT PRIMARY KEY,state TEXT,at REAL,last_ok REAL)""")
    # Un reinicio requiere una nueva respuesta remota para confirmar conexión.
    with db:
        db.execute("DELETE FROM teacher_connections")
    db.execute("""CREATE TABLE IF NOT EXISTS service_status(
        service TEXT PRIMARY KEY, state TEXT, at REAL, last_ok REAL, detail TEXT)""")
    for service in ("polymarket", "laya", "supervisor"):
        record(db, service, "waiting", "Iniciando conexión")


def teacher(db, service, state, success=False):
    now = time.time()
    with db:
        db.execute("""INSERT INTO teacher_connections VALUES(?,?,?,?)
            ON CONFLICT(service) DO UPDATE SET state=excluded.state,at=excluded.at,
                last_ok=coalesce(excluded.last_ok,teacher_connections.last_ok)""",
                   (service,state,now,now if success else None))
