"""Decisión remota a la apertura y ejecución exclusivamente simulada."""
from dataclasses import replace
import json
import time

import live_paper as live
import paper_audit as audit
from paper_stake import capital_and_stake, size_for_budget
from autonomy import number
import service_status


class RemoteAgent(live.Agent):
    def __init__(self,path,brain=None):
        super().__init__(path)
        self.brain = brain
        self.status("waiting" if brain else "unconfigured",
                    "Esperando apertura" if brain else "Configura la API del profesor")
        # Un reinicio no repite una consulta ni una compra pendiente.
        for start, in self.db.execute("SELECT start FROM bet_attempts WHERE status='pending'").fetchall():
            order = self.db.execute("SELECT cost FROM orders WHERE market_id=?",(live.slug(start),)).fetchone()
            self.finish(start,"filled_paper" if order else "rejected",
                        "Recibo recuperado tras reiniciar" if order else "Consulta interrumpida por reinicio; no se repite",
                        actual_cost=order[0] if order else None)

    def status(self,state,detail):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO brain_status VALUES(1,?,?,?,?)",
                            (time.time(),state,detail,self.brain.name if self.brain else "LLM sin configurar"))

    def finish(self,start,status,reason,actual_cost=None,outcome=None):
        with self.db:
            hashed = audit.append(self.db,start,"bet_receipt",dict(status=status,reason=reason,
                                   actual_cost=actual_cost,outcome=outcome))
            self.db.execute("""UPDATE bet_attempts SET at=?,status=?,reason=?,actual_cost=?,
                outcome=coalesce(?,outcome),audit_hash=? WHERE start=?""",
                            (time.time(),status,reason,actual_cost,outcome,hashed,start))
            self.db.execute("UPDATE forecasts SET decision=? WHERE start=?",(reason,start))
            self.emit(start,"Riesgo","Cartera",reason,estado=status,costo=actual_cost,lado=outcome,recibo=hashed)

    def decide(self,start):
        if self.db.execute("SELECT 1 FROM bet_attempts WHERE start=?",(start,)).fetchone():
            return
        now = time.time()
        if start <= now < start+12 and self.brain:
            # La referencia se publica al abrir. Esperar su llegada evita rechazar
            # por una carrera entre el hilo de datos y el corte de apertura.
            target_ready = self.db.execute("SELECT 1 FROM btc_targets WHERE start=? AND seen<=?",(start,now)).fetchone()
            if not target_ready:
                self.status("waiting","Esperando el precio a superar oficial dentro de la ventana de apertura")
                return
        capital,stake = capital_and_stake(self.db)
        with self.db:
            self.db.execute("""INSERT INTO bet_attempts(start,at,status,reason,capital,stake,model)
                VALUES(?,?,'pending','Preparando evidencia de apertura',?,?,?)""",
                            (start,now,str(capital),str(stake),self.brain.name if self.brain else "Sin configurar"))
        try:
            if not start <= now <= start+live.LIMITS.entry_deadline_seconds:
                raise ValueError("Apertura perdida; no se apuesta fuera de los primeros 15 segundos")
            if self.db.execute("SELECT 1 FROM forecasts WHERE start=?",(start,)).fetchone():
                raise ValueError("El intervalo ya fue procesado; no se sustituye su decisión")
            if not self.brain:
                raise ValueError("API del profesor no configurada; se abstiene sin usar modelos locales")
            ticks = self.db.execute("SELECT seen,ts,bid,ask,bid_size,ask_size FROM ticks WHERE start=?",(start,)).fetchall()
            x,available = live.ml.features(ticks,start)  # Indicadores deterministas; ninguna red local.
            target = self.db.execute("SELECT seen,price FROM btc_targets WHERE start=? AND seen<=?",(start,now)).fetchone()
            quote = self.db.execute("""SELECT ts,seen,price FROM btc_twap_quotes
                WHERE ts<=? AND seen<=? ORDER BY ts DESC LIMIT 1""",(start,now)).fetchone()
            if target is None or quote is None or start-quote[0] > 10:
                raise ValueError("Falta referencia oficial o TWAP reciente de Polymarket a la apertura")
            history = [dict(start=t,winner=w,p_up=p,model=v,pnl=pnl) for t,w,p,v,pnl in self.db.execute("""
                SELECT f.start,f.winner,f.p,f.version,o.pnl FROM forecasts f
                LEFT JOIN orders o ON o.market_id=('btc-updown-5m-'||f.start)
                WHERE f.labeled<=? AND f.winner IS NOT NULL ORDER BY f.start DESC LIMIT 8""",(start,))]
            evidence = dict(mercado=live.slug(start),start=start,end=start+300,as_of=now,modo="paper",
                            opening_features=dict(zip(live.ml.FEATURES,x)),inputs_available_at=available,
                            price_to_beat=dict(price=target[1],received=target[0]),
                            btc_twap=dict(timestamp=quote[0],received=quote[1],price=quote[2]),
                            cartera=dict(capital=str(capital),stake=str(stake),stake_fraction="0.03"),
                            resultados_oficiales_previos=history,
                            mensajes=[dict(ref="E0",hecho=f"Libro Up antes de abrir: mid {x[0]:.6f}, spread {x[1]:.6f}."),
                                      dict(ref="E1",hecho=f"TWAP BTC a la apertura {quote[2]}; precio a superar {target[1]}."),
                                      dict(ref="E2",hecho=f"Capital contable {capital} USD; presupuesto incluido costos {stake} USD."),
                                      dict(ref="E3",hecho=f"Historial disponible: {len(history)} resultados oficiales previos. No modifica los pesos del LLM.")])
            hashed = audit.digest(evidence)
            with self.db:
                self.db.execute("INSERT INTO forecasts(start,x,seen,version,eligible) VALUES(?,?,?,'LLM pendiente',0)",
                                (start,live.dump(x),available))
                self.db.execute("""INSERT INTO brain_decisions(start,ready,action,model,status,detail,evidence,evidence_hash)
                    VALUES(?,?,NULL,?,'pending','Consultando API',?,?)""",
                                (start,now,self.brain.name,audit.canonical(evidence),hashed))
                self.db.execute("UPDATE bet_attempts SET evidence_hash=? WHERE start=?",(hashed,start))
                audit.append(self.db,start,"brain_request",dict(evidence=evidence,evidence_hash=hashed))
                self.emit(start,"Libro","LLM","Evidencia de apertura enviada al servidor",evidence_hash=hashed,datos=evidence)
            self.status("busy","LLM remoto analizando la apertura")
            service_status.teacher(self.db,"brain","busy")
            answer = self.brain.decide(evidence,timeout=max(.1,start+15-time.time()))
            ready = time.time()
            response_hash = audit.digest(answer)
            with self.db:
                self.db.execute("""UPDATE brain_decisions SET ready=?,p_up=?,action=?,rationale=?,model=?,status='ok',
                    detail='Respuesta remota válida; puntuación no calibrada',response=?,decision_hash=? WHERE start=?""",
                                (ready,answer["p_up"],answer["action"],answer["reason"],self.brain.name,
                                 audit.canonical(answer),response_hash,start))
                self.db.execute("UPDATE bet_attempts SET model=?,decision_hash=? WHERE start=?",
                                (self.brain.name,response_hash,start))
                self.db.execute("UPDATE forecasts SET p=?,version=?,eligible=? WHERE start=?",
                                (answer["p_up"],"LLM · "+self.brain.name,int(answer["action"]!="abstain"),start))
                audit.append(self.db,start,"brain_response",dict(response=answer,decision_hash=response_hash,model=self.brain.name))
                self.emit(start,"LLM","Riesgo","Decisión remota recibida",accion=answer["action"],p_up=answer["p_up"],
                          justificacion=answer["reason"],refs=answer["refs"],decision_hash=response_hash)
            service_status.teacher(self.db,"brain","ok",True)
            self.status("ok","Respuesta del LLM recibida; se validan límites de simulación")
            if ready > start+15:
                raise ValueError("Respuesta del LLM posterior a la ventana de entrada; no se apuesta")
            if answer["action"] == "abstain":
                self.finish(start,"abstained","Abstención del LLM: "+answer["reason"])
                return
            side = answer["action"]
            probability = number(answer["p_up"]) if side == "Up" else 1-number(answer["p_up"])
            _,view,raw = live.market_for(start)
            live.must_be_open(view)
            book,top,_,ts = live.orderbook(view,side)
            # Recalcular después de la consulta: liquidaciones anteriores pueden haber cambiado el capital.
            capital,stake = capital_and_stake(self.db)
            limit = probability-number(live.LIMITS.minimum_net_edge)
            fill = size_for_budget(raw,book,limit,stake)
            proposal = dict(now=time.time(),start=start,end=start+300,book_time=ts,inputs_available_at=available,
                shares=fill["shares"],p_win=str(probability),max_price=str(limit),total_costs_extra=fill["fees"],
                min_order_size=str(max(number(book["min_order_size"]),number(raw["orderMinSize"]))),
                model_version="LLM · "+self.brain.name,market_id=live.slug(start),outcome=side,
                best_bid=top["best_bid"],asks=[[v["price"],v["size"]] for v in book["asks"]],
                model_approved=True,costs_verified=True,rules_verified=True,market_open=True,
                stake_fraction="0.03",stake_budget=str(stake),capital_basis=str(capital))
            with self.db:
                self.db.execute("UPDATE bet_attempts SET capital=?,stake=?,outcome=? WHERE start=?",
                                (str(capital),str(stake),side,start))
                audit.append(self.db,start,"paper_proposal",proposal)
            result = self.ledger.buy(proposal,replace(live.LIMITS,max_order_cost=str(stake)))
            if result["status"] == "filled_paper":
                self.finish(start,"filled_paper","Apuesta simulada ejecutada y registrada",result["total_cost"],side)
            else:
                self.finish(start,"rejected",result["reason"],outcome=side)
        except live.API_ERRORS as exc:
            reason = str(exc)
            with self.db:
                self.db.execute("INSERT OR IGNORE INTO forecasts(start,decision) VALUES(?,?)",(start,reason))
                self.db.execute("UPDATE brain_decisions SET status=CASE WHEN status='pending' THEN 'error' ELSE status END,detail=? WHERE start=?",(reason,start))
            if self.db.execute("SELECT status FROM brain_decisions WHERE start=?",(start,)).fetchone() == ("error",):
                self.status("error",reason)
                service_status.teacher(self.db,"brain","error")
            self.finish(start,"rejected",reason)

    def train(self,start):
        """El LLM se consulta en el servidor; este modo no entrena modelos."""
        return

    def resolve(self,now):
        super().resolve(now)
        # Registrar una sola vez la liquidación definitiva en la cadena auditable.
        rows = self.db.execute("""SELECT f.start,f.winner,o.cost,o.payout,o.pnl,o.settled_at FROM forecasts f
            JOIN orders o ON o.market_id=('btc-updown-5m-'||f.start)
            JOIN bet_attempts b ON b.start=f.start WHERE o.settled_at IS NOT NULL AND NOT EXISTS(
                SELECT 1 FROM audit_events a WHERE a.start=f.start AND a.kind='settlement')""").fetchall()
        for start,winner,cost,payout,pnl,at in rows:
            with self.db:
                audit.append(self.db,start,"settlement",dict(winner=winner,cost=cost,payout=payout,pnl=pnl,settled_at=at))
                self.emit(start,"Resolución","LLM","Resultado oficial añadido al historial para próximas consultas",
                          ganador=winner,costo=cost,pago=payout,pnl=pnl)
