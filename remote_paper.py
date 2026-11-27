"""Decisión remota a la apertura y ejecución exclusivamente simulada."""
from dataclasses import replace
import json
import time

import live_paper as live
import paper_audit as audit
from paper_stake import capital_and_stake, size_for_budget
from autonomy import number
import opening_memory
import service_status
import btc_reference
from remote_brain import PROMPT_VERSION, EXPLORATION_VERSION


class RemoteAgent(live.Agent):
    def __init__(self,path,brain=None,exploration=False):
        super().__init__(path)
        self.exploration = exploration
        with self.db:
            self.db.execute("CREATE TABLE IF NOT EXISTS paper_mode(id INTEGER PRIMARY KEY, exploration INTEGER NOT NULL)")
            if exploration and not self.db.execute("SELECT 1 FROM paper_mode").fetchone() and self.db.execute("SELECT 1 FROM bet_attempts LIMIT 1").fetchone():
                raise ValueError("La cartera ya contiene intentos de la estrategia habitual")
            self.db.execute("INSERT OR IGNORE INTO paper_mode VALUES(1,?)",(int(exploration),))
            if self.db.execute("SELECT exploration FROM paper_mode WHERE id=1").fetchone()[0] != exploration:
                raise ValueError("La cartera pertenece a otro modo de simulación")
        self.brain = brain
        self.status("waiting" if brain else "unconfigured",
                    "Esperando apertura" if brain else "Configura la API del profesor")
        # Un reinicio no repite una consulta ni una compra pendiente.
        for start, in self.db.execute("SELECT start FROM bet_attempts WHERE status='pending'").fetchall():
            order = self.db.execute("SELECT cost FROM orders WHERE market_id=?",(live.slug(start),)).fetchone()
            with self.db:
                if not order:
                    self.db.execute("UPDATE brain_decisions SET status='error',response_valid=0,detail='Consulta interrumpida por reinicio; duración desconocida' WHERE start=? AND status='pending'",(start,))
                    audit.append(self.db,start,"brain_interrupted",dict(reason="Reinicio sin respuesta; no se inventa tiempo de finalización"))
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
            target_ready = self.db.execute("SELECT 1 FROM btc_targets WHERE start=? AND seen<=? AND source IN (?,?)",(start,now,btc_reference.GAMMA_SOURCE,btc_reference.OPENING_TWAP_SOURCE)).fetchone()
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
            target = self.db.execute("SELECT seen,price,source FROM btc_targets WHERE start=? AND seen<=? AND source IN (?,?)",(start,now,btc_reference.GAMMA_SOURCE,btc_reference.OPENING_TWAP_SOURCE)).fetchone()
            quote = self.db.execute("""SELECT ts,seen,price FROM btc_twap_quotes
                WHERE ts<=? AND seen<=? ORDER BY ts DESC LIMIT 1""",(start,now)).fetchone()
            if target is None or quote is None or start-quote[0] > 10:
                raise ValueError("Falta referencia oficial o TWAP reciente de Polymarket a la apertura")
            history = [dict(start=t,winner=w,p_up=p,model=v,pnl=pnl) for t,w,p,v,pnl in self.db.execute("""
                SELECT f.start,f.winner,f.p,f.version,o.pnl FROM forecasts f
                LEFT JOIN orders o ON o.market_id=('btc-updown-5m-'||f.start)
                WHERE f.labeled<=? AND f.winner IS NOT NULL ORDER BY f.start DESC LIMIT 8""",(start,))]
            version = EXPLORATION_VERSION if self.exploration else PROMPT_VERSION
            evidence = dict(mercado=live.slug(start),start=start,end=start+300,as_of=now,modo="exploracion_paper" if self.exploration else "paper",prompt_version=version,
                            opening_features=dict(zip(live.ml.FEATURES,x)),inputs_available_at=available,
                            price_to_beat=dict(price=target[1],received=target[0],source=target[2],reference_ts=start,
                                               gamma_confirmed=target[2]==btc_reference.GAMMA_SOURCE),
                            btc_twap=dict(timestamp=quote[0],received=quote[1],price=quote[2]),
                            cartera=dict(capital=str(capital),stake=str(stake),stake_fraction="0.03"),
                            resultados_oficiales_previos=history,
                            mensajes=[dict(ref="E0",hecho=f"Libro Up antes de abrir: mid {x[0]:.6f}, spread {x[1]:.6f}."),
                                      dict(ref="E1",hecho=f"TWAP BTC a la apertura {quote[2]}; precio a superar {target[1]}."),
                                      dict(ref="E2",hecho=f"Capital contable {capital} USD; presupuesto incluido costos {stake} USD."),
                                      dict(ref="E3",hecho=f"Historial disponible: {len(history)} resultados oficiales previos. No modifica los pesos del LLM.")])
            trend = self.db.execute("SELECT ts,price FROM btc_twap_quotes WHERE ts BETWEEN ? AND ? AND seen<=? ORDER BY ts DESC LIMIT 60",(start-60,start,now)).fetchall()
            if trend:
                evidence["mensajes"].append(dict(ref="E4",hecho=f"TWAP BTC antes de abrir, en orden temporal (segundo, USD): {trend[::-1]}"))
            version = opening_memory.add_context(self.db,start,x,evidence)
            hashed = audit.digest(evidence)
            requested_at=time.time()
            with self.db:
                self.db.execute("INSERT INTO forecasts(start,x,seen,version,eligible) VALUES(?,?,?,'LLM pendiente',0)",
                                (start,live.dump(x),available))
                self.db.execute("""INSERT INTO brain_decisions(start,ready,action,model,status,detail,evidence,evidence_hash)
                    VALUES(?,?,NULL,?,'pending','Consultando API',?,?)""",
                                (start,now,self.brain.name,audit.canonical(evidence),hashed))
                self.db.execute("UPDATE bet_attempts SET evidence_hash=? WHERE start=?",(hashed,start))
                self.db.execute("UPDATE brain_decisions SET requested_at=?,prompt_version=? WHERE start=?",
                                (requested_at,version,start))
                audit.append(self.db,start,"brain_request",dict(evidence=evidence,evidence_hash=hashed,
                             requested_at=requested_at,prompt_version=version))
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
                self.db.execute("UPDATE brain_decisions SET completed_at=?,response_valid=1 WHERE start=?",(ready,start))
                self.db.execute("UPDATE forecasts SET p=?,version=?,eligible=? WHERE start=?",
                                (answer["p_up"],"LLM · "+self.brain.name,int(answer["action"]!="abstain"),start))
                audit.append(self.db,start,"brain_response",dict(response=answer,decision_hash=response_hash,model=self.brain.name,
                             completed_at=ready,response_valid=True))
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
            current_target=self.db.execute("SELECT price FROM btc_targets WHERE start=? AND source IN (?,?)",
                (start,btc_reference.GAMMA_SOURCE,btc_reference.OPENING_TWAP_SOURCE)).fetchone()
            if current_target is None or abs(number(current_target[0])-number(target[1]))>number("0.000001"):
                raise ValueError("El precio a superar se corrigió durante la consulta; se rechaza para no apostar con una referencia anterior")
            probability = number(answer["p_up"]) if side == "Up" else 1-number(answer["p_up"])
            _,view,raw = live.market_for(start)
            live.must_be_open(view)
            book,top,_,ts = live.orderbook(view,side)
            # Recalcular después de la consulta: liquidaciones anteriores pueden haber cambiado el capital.
            capital,stake = capital_and_stake(self.db)
            limit = probability-number(live.LIMITS.minimum_net_edge)
            if self.exploration:
                limit = number(top['best_ask'])  # Únicamente liquidez al mejor precio observado.
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
            result = self.ledger.buy(proposal,replace(live.LIMITS,max_order_cost=str(stake),require_net_edge=not self.exploration))
            if result["status"] == "filled_paper":
                self.finish(start,"filled_paper","Apuesta simulada ejecutada y registrada",result["total_cost"],side)
            else:
                self.finish(start,"rejected",result["reason"],outcome=side)
        except live.API_ERRORS as exc:
            reason = str(exc)
            with self.db:
                failed_at=time.time()
                pending_call=self.db.execute("SELECT 1 FROM brain_decisions WHERE start=? AND requested_at IS NOT NULL AND completed_at IS NULL",(start,)).fetchone()
                if pending_call:
                    audit.append(self.db,start,"brain_failure",dict(completed_at=failed_at,response_valid=False,reason=reason))
                self.db.execute("""UPDATE brain_decisions SET completed_at=?,response_valid=0
                    WHERE start=? AND requested_at IS NOT NULL AND completed_at IS NULL""",(failed_at,start))
                self.db.execute("INSERT OR IGNORE INTO forecasts(start,decision) VALUES(?,?)",(start,reason))
                self.db.execute("UPDATE brain_decisions SET status=CASE WHEN status='pending' THEN 'error' ELSE status END,detail=? WHERE start=?",(reason,start))
            if self.db.execute("SELECT status FROM brain_decisions WHERE start=?",(start,)).fetchone() == ("error",):
                self.status("error",reason)
                service_status.teacher(self.db,"brain","error")
            self.finish(start,"rejected",reason)

    def step(self):
        start = super().step()
        now = time.time()
        if self.brain and start+20 <= now < start+285 and now >= getattr(self,"next_review",0):
            position = self.db.execute("SELECT outcome,shares,cost FROM orders WHERE market_id=? AND settled_at IS NULL",(live.slug(start),)).fetchone()
            if position:
                self.next_review = now+30
                try:
                    self.review_position(start,*position)
                except live.API_ERRORS as exc:
                    with self.db:
                        audit.append(self.db,start,"position_review_error",dict(reason=str(exc)))
        return start

    def review_position(self,start,side,shares,cost):
        from paper_stake import sale_quote
        _,view,raw = live.market_for(start)
        live.must_be_open(view)
        book,_,_,_ = live.orderbook(view,side)
        indicative = sale_quote(raw,book,shares)
        target = self.db.execute("SELECT price FROM btc_targets WHERE start=? AND source IN (?,?)",
            (start,btc_reference.GAMMA_SOURCE,btc_reference.OPENING_TWAP_SOURCE)).fetchone()
        ticks = self.db.execute("SELECT ts,price FROM btc_twap_quotes WHERE ts BETWEEN ? AND ? ORDER BY ts DESC LIMIT 60",
            (start,time.time())).fetchall()
        if not target or not ticks or time.time()-ticks[0][0]>10:
            raise ValueError("Revisión pendiente de referencia y TWAP recientes")
        evidence = dict(mercado=live.slug(start),modo="revision_paper",start=start,end=start+300,as_of=time.time(),
            mensajes=[dict(ref="E0",hecho=f"Objetivo BTC {target[0]}; observaciones TWAP (segundo, USD) en orden temporal: {ticks[::-1]}"),
                      dict(ref="E1",hecho=f"Posición {side}, {shares} participaciones, costo {cost} USD, venta neta estimada {indicative} USD.")])
        answer = self.brain.decide(evidence,timeout=min(10,max(.1,start+300-time.time())))
        with self.db:
            audit.append(self.db,start,"position_review",dict(model=self.brain.name,evidence=evidence,response=answer))
            self.emit(start,"LLM","Riesgo","Reevaluación de posición",accion=answer["action"],motivo=answer["reason"])
        if answer["action"] in ("abstain",side) or time.time()>=start+300:
            return
        book,_,_,ts = live.orderbook(view,side)
        net = sale_quote(raw,book,shares)
        p = number(answer["p_up"])
        hold = number(shares)*(p if side=="Up" else 1-p)
        if net <= hold:
            return
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            at = time.time()
            if at >= start+300 or at-ts > live.LIMITS.max_book_age_seconds:
                return
            pnl = str(net-number(cost))
            changed = self.db.execute("UPDATE orders SET settled_at=?,payout=?,pnl=? WHERE market_id=? AND settled_at IS NULL",
                (at,str(net),pnl,live.slug(start))).rowcount
            if changed:
                cash = number(self.db.execute("SELECT cash FROM account WHERE id=1").fetchone()[0])
                self.db.execute("UPDATE account SET cash=? WHERE id=1",(str(cash+net),))
                audit.append(self.db,start,"early_sale",dict(cost=cost,payout=str(net),pnl=pnl,settled_at=at,
                    shares=shares,side=side,bids=book["bids"],book_time=ts,hold_value=str(hold),reason=answer["reason"]))
                self.emit(start,"Riesgo","Cartera","Venta anticipada simulada",ingreso=str(net),pnl=pnl)

    def train(self,start):
        """El LLM se consulta en el servidor; este modo no entrena modelos."""
        return

    def resolve(self,now):
        super().resolve(now)
        # Registrar una sola vez la liquidación definitiva en la cadena auditable.
        rows = self.db.execute("""SELECT f.start,f.winner,o.cost,o.payout,o.pnl,o.settled_at FROM forecasts f
            JOIN orders o ON o.market_id=('btc-updown-5m-'||f.start)
            JOIN bet_attempts b ON b.start=f.start WHERE o.settled_at IS NOT NULL AND NOT EXISTS(
                SELECT 1 FROM audit_events a WHERE a.start=f.start AND a.kind IN ('settlement','early_sale'))""").fetchall()
        for start,winner,cost,payout,pnl,at in rows:
            with self.db:
                audit.append(self.db,start,"settlement",dict(winner=winner,cost=cost,payout=payout,pnl=pnl,settled_at=at))
                self.emit(start,"Resolución","LLM","Resultado oficial añadido al historial para próximas consultas",
                          ganador=winner,costo=cost,pago=payout,pnl=pnl)
