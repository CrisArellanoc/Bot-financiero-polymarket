"""Evaluación descriptiva de decisiones remotas y cartera. No entrena ni decide apuestas."""
from collections import defaultdict, OrderedDict
from datetime import datetime, timezone
from decimal import Decimal
import json
import math
import random
import statistics
from threading import Lock
import time

REVISION = "evaluation-v1"
_cache = OrderedDict()
_lock = Lock()


def mean(values):
    return statistics.mean(values) if values else None


def ratio(a,b):
    return a/b if b else None


def finite(value):
    return type(value) in (int,float) and math.isfinite(value)


def probability(value):
    return finite(value) and 0 <= value <= 1


def decoded(value):
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed,dict) else {}
    except (ValueError,TypeError):
        return {}


def day(timestamp):
    return datetime.fromtimestamp(timestamp,timezone.utc).strftime("%Y-%m-%d")


def percentile(values,q):
    return sorted(values)[max(0,math.ceil(q*len(values))-1)] if values else None


def block_interval(pairs):
    """IC descriptivo: remuestrear días completos, preservando dependencia intradía."""
    blocks = defaultdict(list)
    for timestamp,value in pairs:
        blocks[day(timestamp)].append(value)
    if len(blocks)<2:
        return dict(low=None,high=None,blocks=len(blocks),method="bootstrap diario UTC",resamples=0)
    summaries = [(sum(v),len(v)) for v in blocks.values()]
    rng = random.Random(123)
    samples = []
    for _ in range(300):
        selected = rng.choices(summaries,k=len(summaries))
        samples.append(sum(v for v,n in selected)/sum(n for v,n in selected))
    return dict(low=percentile(samples,.025),high=percentile(samples,.975),blocks=len(blocks),
                method="bootstrap diario UTC",resamples=300)


def prediction_scores(scored):
    """scored: start, p, y, market, prior, action. Abstenciones numéricas puntúan Brier."""
    pairs = [r for r in scored if probability(r.get("market"))]
    brier = lambda rows,key: mean([(r[key]-r["y"])**2 for r in rows])
    bs,market = brier(scored,"p"),brier(pairs,"market")
    common = brier(pairs,"p")
    bins = []
    for index in range(10):
        rows = [r for r in scored if min(9,int(r["p"]*10))==index]
        bins.append(dict(lower=index/10,upper=(index+1)/10,n=len(rows),
                         predicted=mean([r["p"] for r in rows]),observed=mean([r["y"] for r in rows])))
    ece = (sum(b["n"]*abs(b["predicted"]-b["observed"]) for b in bins if b["n"])/len(scored)) if scored else None
    actions = [r for r in scored if r["action"] in ("Up","Down")]
    tp = sum(r["action"]=="Up" and r["y"]==1 for r in actions)
    tn = sum(r["action"]=="Down" and r["y"]==0 for r in actions)
    fp = sum(r["action"]=="Up" and r["y"]==0 for r in actions)
    fn = sum(r["action"]=="Down" and r["y"]==1 for r in actions)
    recall_up,recall_down = ratio(tp,tp+fn),ratio(tn,tn+fp)
    action_pairs = [r for r in pairs if r["action"] in ("Up","Down")]
    return dict(n=len(scored),brier=bs,log_loss=mean([
        -math.log(max(1e-15,min(1-1e-15,r["p"] if r["y"] else 1-r["p"]))) for r in scored]),
        market_n=len(pairs),brier_common=common,market_brier=market,
        brier_skill=1-common/market if market is not None and market>0 else None,
        prior_brier=brier(scored,"prior"),calibration=bins,ece=ece,
        classification_n=len(actions),accuracy=ratio(tp+tn,len(actions)),
        balanced_accuracy=(recall_up+recall_down)/2 if recall_up is not None and recall_down is not None else None,
        precision_up=ratio(tp,tp+fp),recall_up=recall_up,precision_down=ratio(tn,tn+fn),recall_down=recall_down,
        f1_up=ratio(2*tp,2*tp+fp+fn),f1_down=ratio(2*tn,2*tn+fp+fn),
        confusion=dict(actual_up_pred_up=tp,actual_up_pred_down=fn,actual_down_pred_up=fp,actual_down_pred_down=tn),
        market_accuracy=mean([int((r["market"]>=.5)==r["y"]) for r in action_pairs]),
        action_market_n=len(action_pairs),prior_accuracy=mean([int((r["prior"]>=.5)==r["y"]) for r in actions]),
        improvement_ci=block_interval([(r["start"],(r["market"]-r["y"])**2-(r["p"]-r["y"])**2) for r in pairs]))


def trading_scores(orders):
    settled = sorted((r for r in orders if r["settled_at"] is not None and r["pnl"] is not None),
                     key=lambda r:(r["settled_at"],r["market_id"]))
    amounts = [Decimal(r["pnl"]) for r in settled]
    gains = sum((max(v,Decimal(0)) for v in amounts),Decimal(0))
    losses = sum((max(-v,Decimal(0)) for v in amounts),Decimal(0))
    streak=longest=0
    for value in amounts:
        streak = streak+1 if value<0 else 0
        longest = max(longest,streak)
    return dict(orders=len(orders),settled=len(settled),pending=len(orders)-len(settled),
                net=float(sum(amounts,Decimal(0))),mean_net=float(sum(amounts)/len(amounts)) if amounts else None,
                wins=sum(v>0 for v in amounts),losses=sum(v<0 for v in amounts),
                win_rate=ratio(sum(v>0 for v in amounts),len(amounts)),gains=float(gains),gross_losses=float(losses),
                profit_factor=float(gains/losses) if losses else None,
                profit_factor_status="measured" if losses else "no_losses" if gains else "no_results",
                max_losing_streak=longest,mean_net_ci=block_interval([(r["settled_at"],float(r["pnl"])) for r in settled]))


def equity_scores(orders,cash):
    exposure = sum((Decimal(r["cost"]) for r in orders if r["settled_at"] is None),Decimal(0))
    settled = [r for r in orders if r["settled_at"] is not None and r["pnl"] is not None]
    initial = Decimal(cash)+exposure-sum((Decimal(r["pnl"]) for r in settled),Decimal(0))
    equity=peak=initial
    max_usd=max_pct=Decimal(0)
    events = defaultdict(lambda:Decimal(0))
    for row in settled:
        events[row["settled_at"]] += Decimal(row["pnl"])
    curve = [dict(at=None,capital=float(initial),drawdown=0)]
    for at,pnl in sorted(events.items()):
        equity += pnl
        peak = max(peak,equity)
        max_usd = max(max_usd,peak-equity)
        drop = (peak-equity)/peak if peak>0 else Decimal(0)
        max_pct = max(max_pct,drop)
        curve.append(dict(at=at,capital=float(equity),drawdown=float(drop)))
    # Compactación visual solamente. El cálculo usa todas las liquidaciones.
    if len(curve)>200:
        indexes = sorted({round(i*(len(curve)-1)/199) for i in range(200)})
        curve = [curve[i] for i in indexes]
    return dict(initial=float(initial),capital=float(equity),exposure=float(exposure),settled=len(settled),
                max_drawdown_usd=float(max_usd),max_drawdown_pct=float(max_pct),curve=curve,
                scope="Toda la cartera, incluidos registros anteriores; capital realizado, sin valoración de posiciones abiertas")


def period_scores(scores,kind):
    buckets=defaultdict(list)
    for row in scores:
        if kind=="day":
            key=day(row["start"])
        elif kind=="hour":
            key=datetime.fromtimestamp(row["start"],timezone.utc).strftime("%H:00")
        else:
            vol=row.get("book_volatility")
            key="sin dato" if vol is None else "baja (<0.005)" if vol<.005 else "media (0.005 a <0.02)" if vol<.02 else "alta (>=0.02)"
        buckets[key].append(row)
    result=[]
    for key,rows in sorted(buckets.items()):
        paired=[r for r in rows if probability(r["market"])]
        result.append(dict(period=key,n=len(rows),brier=mean([(r["p"]-r["y"])**2 for r in rows]),
                           market_n=len(paired),brier_common=mean([(r["p"]-r["y"])**2 for r in paired]),
                           market_brier=mean([(r["market"]-r["y"])**2 for r in paired])))
    return result[-30:] if kind=="day" else result


def evaluate(db,now=None):
    now = time.time() if now is None else now
    records = [dict(r) for r in db.execute("""SELECT b.*,f.winner,f.labeled FROM brain_decisions b
        LEFT JOIN forecasts f USING(start) ORDER BY b.start""")]
    attempts = [dict(r) for r in db.execute("SELECT * FROM bet_attempts ORDER BY start")]
    orders = [dict(r) for r in db.execute("SELECT * FROM orders ORDER BY end,market_id")]
    proposals = {r[0]:decoded(r[1]) for r in db.execute("SELECT start,payload FROM audit_events WHERE kind='paper_proposal' ORDER BY id")}
    # Referencia histórica estrictamente anterior, usando solo etiquetas publicadas.
    labels = sorted((r[0],r[1]) for r in db.execute("SELECT labeled,winner FROM forecasts WHERE winner IN ('Up','Down') AND labeled IS NOT NULL"))
    count=up=index=0
    buckets = defaultdict(lambda:dict(records=[],attempts=[],scored=[],orders=[]))
    all_scored=[]
    record_by_start={}
    for record in records:
        while index<len(labels) and labels[index][0]<=record["start"]:
            count+=1;up+=labels[index][1]=="Up";index+=1
        record["prior"] = (up+1)/(count+2)
        record["version"] = record.get("prompt_version") or "legacy-unknown"
        key = (record["model"] or "Modelo desconocido",record["version"])
        record_by_start[record["start"]]=(record,key)
        buckets[key]["records"].append(record)
        validated = record.get("response_valid")==1 or (record.get("response_valid") is None and record["status"]=="ok" and bool(record["response"]))
        on_time = validated and finite(record["ready"]) and record["start"]<=record["ready"]<=record["start"]+15
        record["validated"],record["on_time"] = bool(validated),bool(on_time)
        evidence=decoded(record["evidence"])
        observed=evidence.get("inputs_available_at")
        known_inputs=finite(observed) and observed<=record["start"]
        labeled = finite(record["labeled"]) and record["start"]+300<=record["labeled"]<=now and record["winner"] in ("Up","Down")
        if not (on_time and known_inputs and labeled and probability(record["p_up"])):
            continue
        features=evidence.get("opening_features") or {}
        market=features.get("mid_up") if isinstance(features,dict) else None
        row=dict(start=record["start"],p=record["p_up"],y=int(record["winner"]=="Up"),
                 market=market,prior=record["prior"],action=record["action"],
                 book_volatility=features.get("volatility") if isinstance(features,dict) and finite(features.get("volatility")) and features["volatility"]>=0 else None)
        all_scored.append(row);buckets[key]["scored"].append(row)
    for attempt in attempts:
        found=record_by_start.get(attempt["start"])
        key=found[1] if found else (attempt["model"] or "Sin modelo","sin-consulta")
        buckets[key]["attempts"].append(attempt)
    remote_orders=[]
    attempts_by_market={f"btc-updown-5m-{r['start']}":r for r in attempts}
    for order in orders:
        attempt=attempts_by_market.get(order["market_id"])
        if attempt is None:
            continue
        found=record_by_start.get(attempt["start"])
        key=found[1] if found else (attempt["model"] or "Sin modelo","sin-consulta")
        buckets[key]["orders"].append(order);remote_orders.append(order)

    def summarize(rows,observed,scores,trades):
        completed=[r for r in rows if finite(r.get("requested_at")) and finite(r.get("completed_at")) and r["completed_at"]>=r["requested_at"]]
        latency=[r["completed_at"]-r["requested_at"] for r in completed]
        directional=[r for r in rows if r["on_time"] and r["action"] in ("Up","Down")]
        directional_starts={r["start"] for r in directional}
        statuses={s:sum(r["status"]==s for r in observed) for s in ("pending","filled_paper","rejected","abstained")}
        return dict(prediction=prediction_scores(scores),trading=trading_scores(trades),
                    daily=period_scores(scores,"day"),hourly=period_scores(scores,"hour"),regimes=period_scores(scores,"volatility"),
                    coverage=dict(intervals=len(observed),valid_on_time=sum(r["on_time"] for r in rows),
                        signals=len(directional),signal_rate=ratio(len(directional),len(observed)),
                        abstentions=sum(r["on_time"] and r["action"]=="abstain" for r in rows),
                        api_failures=sum(r.get("response_valid")==0 for r in rows),
                        late_responses=sum(r["validated"] and not r["on_time"] for r in rows),
                        risk_rejections=sum(a["status"]=="rejected" and a["start"] in directional_starts for a in observed),
                        statuses=statuses),
                    api=dict(requests=sum(finite(r.get("requested_at")) for r in rows),completed=len(completed),
                        valid=sum(r["validated"] for r in completed),valid_rate=ratio(sum(r["validated"] for r in completed),len(completed)),
                        on_time_rate=ratio(sum(r["on_time"] for r in completed),len(completed)),
                        median_seconds=statistics.median(latency) if latency else None,p95_seconds=percentile(latency,.95),
                        interrupted=sum(finite(r.get("requested_at")) and not finite(r.get("completed_at")) and r["status"]=="error" for r in rows),
                        timing_unknown=sum(not finite(r.get("requested_at")) for r in rows)),
                    mean_estimated_net=mean([float(Decimal(proposals[a["start"]]["p_win"])*Decimal(proposals[a["start"]]["shares"])-Decimal(a["actual_cost"]))
                        for a in observed if a["status"]=="filled_paper" and a["actual_cost"] is not None and a["start"] in proposals]))

    groups=[]
    for (model,version),bucket in buckets.items():
        groups.append(dict(key=f"{model}|{version}",model=model,prompt_version=version,
                           **summarize(bucket["records"],bucket["attempts"],bucket["scored"],bucket["orders"])))
    cash=db.execute("SELECT cash FROM account WHERE id=1").fetchone()[0]
    return dict(revision=REVISION,overall=summarize(records,attempts,all_scored,remote_orders),groups=groups,
                portfolio=equity_scores(orders,cash),
                note="Evaluación prospectiva descriptiva. No calibra ni modifica el LLM. Versiones comparadas en periodos distintos no prueban superioridad.")


def snapshot(db):
    """Reutilizar cálculos mientras solo cambian cotizaciones, no decisiones o resultados."""
    path=db.execute("PRAGMA database_list").fetchone()[2]
    key=(tuple(db.execute("SELECT count(*),max(id) FROM audit_events").fetchone()),
         tuple(db.execute("SELECT count(*),max(ready),max(completed_at),sum(response_valid) FROM brain_decisions").fetchone()),
         tuple(db.execute("SELECT count(*),max(labeled),count(labeled) FROM forecasts").fetchone()),
         tuple(db.execute("SELECT count(*),max(at) FROM bet_attempts").fetchone()),
         tuple(db.execute("SELECT count(*),count(settled_at),max(settled_at),sum(CAST(pnl AS REAL)) FROM orders").fetchone()))
    # Las conexiones en memoria no tienen identidad persistente y no comparten caché.
    if not path:
        return evaluate(db)
    with _lock:
        stored=_cache.get(path)
        if stored and stored[0]==key:
            return stored[1]
        result=evaluate(db)
        _cache[path]=(key,result);_cache.move_to_end(path)
        while len(_cache)>8:
            _cache.popitem(last=False)
        return result
