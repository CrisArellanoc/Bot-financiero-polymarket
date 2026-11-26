"""Recibos locales de simulación. Hashes de integridad, sin claves ni respuestas privadas."""
import hashlib
import json
import sqlite3
import time
from contextlib import closing


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def tables(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS brain_decisions(start INTEGER PRIMARY KEY,ready REAL,
            p_up REAL,action TEXT,rationale TEXT,model TEXT,status TEXT,detail TEXT,
            evidence TEXT,response TEXT,evidence_hash TEXT,decision_hash TEXT);
        CREATE TABLE IF NOT EXISTS brain_status(id INTEGER PRIMARY KEY CHECK(id=1),at REAL,state TEXT,detail TEXT,model TEXT);
        CREATE TABLE IF NOT EXISTS bet_attempts(id INTEGER PRIMARY KEY,start INTEGER UNIQUE,at REAL,
            status TEXT,reason TEXT,capital TEXT,stake TEXT,actual_cost TEXT,outcome TEXT,model TEXT,
            evidence_hash TEXT,decision_hash TEXT,audit_hash TEXT);
        CREATE TABLE IF NOT EXISTS audit_events(id INTEGER PRIMARY KEY,start INTEGER,at REAL,
            kind TEXT,payload TEXT,previous_hash TEXT,hash TEXT);
    """)


def append(db,start,kind,payload):
    last = db.execute("SELECT id,hash FROM audit_events ORDER BY id DESC LIMIT 1").fetchone()
    index,previous = (last[0]+1,last[1]) if last else (1,"0"*64)
    at = float(time.time())
    record = dict(id=index,start=start,at=at,kind=kind,payload=payload,previous_hash=previous)
    hashed = digest(record)
    db.execute("INSERT INTO audit_events VALUES(?,?,?,?,?,?,?)",
               (index,start,at,kind,canonical(payload),previous,hashed))
    return hashed


def verify(db):
    previous = "0"*64
    for index,start,at,kind,payload,prev,hashed in db.execute("SELECT * FROM audit_events ORDER BY id"):
        try:
            if prev != previous or digest(dict(id=index,start=start,at=at,kind=kind,
                payload=json.loads(payload),previous_hash=prev)) != hashed:
                return False
        except (ValueError,TypeError):
            return False
        previous = hashed
    return True


def export(path,start=None):
    with closing(sqlite3.connect(path,timeout=3)) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        clause,args = (" WHERE start=?",(start,)) if start is not None else ("",())
        events = [dict(r) for r in db.execute("SELECT * FROM audit_events"+clause+" ORDER BY id",args)]
        for event in events:
            try:
                event["payload"] = json.loads(event["payload"])
            except (ValueError,TypeError):
                event["payload"] = {"invalid_json":True}
        decisions = [dict(r) for r in db.execute("SELECT * FROM brain_decisions"+clause,args)]
        consistent = True
        for row in decisions:
            try:
                row["evidence"] = json.loads(row["evidence"])
                row["response"] = json.loads(row["response"]) if row["response"] else None
            except (ValueError,TypeError):
                row["evidence"],row["response"],consistent = None,None,False
            consistent &= digest(row["evidence"]) == row["evidence_hash"]
            if row["response"] is not None:
                consistent &= digest(row["response"]) == row["decision_hash"]
            requests = [e for e in events if e["start"]==row["start"] and e["kind"]=="brain_request"]
            replies = [e for e in events if e["start"]==row["start"] and e["kind"]=="brain_response"]
            consistent &= bool(requests and isinstance(requests[-1]["payload"],dict) and requests[-1]["payload"].get("evidence_hash")==row["evidence_hash"])
            if row["response"] is not None:
                consistent &= bool(replies and isinstance(replies[-1]["payload"],dict) and replies[-1]["payload"].get("decision_hash")==row["decision_hash"])
        attempts = [dict(r) for r in db.execute("SELECT * FROM bet_attempts"+clause+" ORDER BY id",args)]
        orders = [dict(r) for r in db.execute("SELECT * FROM orders"+
                  (" WHERE market_id=?" if start is not None else ""),
                  (f"btc-updown-5m-{start}",) if start is not None else ())]
        resolutions = [dict(r) for r in db.execute("SELECT start,winner,labeled FROM forecasts"+clause,args)]
        receipts_valid = True
        for attempt in attempts:
            receipts = [e for e in events if e["start"]==attempt["start"] and e["kind"]=="bet_receipt"]
            if attempt["status"] != "pending":
                receipts_valid &= bool(receipts and receipts[-1]["hash"]==attempt["audit_hash"] and
                                       receipts[-1]["payload"]["status"]==attempt["status"])
            order = next((r for r in orders if r["market_id"]==f"btc-updown-5m-{attempt['start']}"),None)
            if attempt["status"]=="filled_paper":
                receipts_valid &= bool(order and order["cost"]==attempt["actual_cost"] and
                                       order["outcome"]==attempt["outcome"])
            if order and order["settled_at"] is not None:
                settlements = [e for e in events if e["start"]==attempt["start"] and e["kind"]=="settlement"]
                receipts_valid &= bool(settlements and all(settlements[-1]["payload"][k]==order[k]
                                           for k in ("cost","payout","pnl","settled_at")))
        return dict(mode="paper",chain_verified=verify(db),decision_hashes_verified=bool(consistent),
                    receipts_verified=bool(receipts_valid),audit_coverage="recorded" if attempts else "no_remote_receipt",
                    explanation="Cadena SHA-256 local: detecta modificaciones; no es una firma externa ni prueba de órdenes reales.",
                    events=events,decisions=decisions,attempts=attempts,orders=orders,resolutions=resolutions)
