"""Componentes Paper: verificación de compra, contabilidad y etiquetas maduras.

No envía órdenes reales, genera predicciones ni conecta un stream.
Los importes son unidades virtuales; los costos se suministran explícitamente.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import math
import sqlite3


def number(value):
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Valor numérico inválido") from exc
    if not result.is_finite():
        raise ValueError("Valor no finito")
    return result


@dataclass(frozen=True)
class Limits:
    max_order_cost: str
    max_total_exposure: str
    max_daily_loss_budget: str
    minimum_net_edge: str
    max_spread: str
    max_book_age_seconds: float
    entry_deadline_seconds: float

    require_net_edge: bool = True
    enforce_daily_loss: bool = True

    def validate(self):
        for name in ('max_order_cost', 'max_total_exposure', 'max_daily_loss_budget', 'minimum_net_edge', 'max_spread'):
            if number(getattr(self, name)) <= 0:
                raise ValueError(f"Límite inválido: {name}")
        for name in ('max_book_age_seconds', 'entry_deadline_seconds'):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"Límite inválido: {name}")


def evaluate_buy(proposal, limits, cash, exposure, daily_losses):
    """Retorna un precio de ejecución Paper; faltantes/errores bloquean la compra.

    p_win corresponde al resultado elegido, no siempre a Up. model_approved y
    costs_verified son contratos del llamador; no prueban por sí mismos calidad.
    asks = niveles ejecutables del resultado elegido; costos extra en moneda.
    """
    limits.validate()
    for flag in ('model_approved', 'costs_verified', 'market_open', 'rules_verified'):
        if proposal.get(flag) is not True:
            raise ValueError(f"Verificación pendiente: {flag}")
    if proposal.get('outcome') not in ('Up', 'Down') or not proposal.get('model_version') or not proposal.get('market_id'):
        raise ValueError("Faltan identidad del mercado, modelo o resultado")
    now, start, end, book_time, inputs_time = [float(proposal[k]) for k in
        ('now', 'start', 'end', 'book_time', 'inputs_available_at')]
    if not all(math.isfinite(t) for t in (now, start, end, book_time, inputs_time)):
        raise ValueError("Tiempos inválidos")
    if end - start != 300 or not start <= now < end or now - start > limits.entry_deadline_seconds:
        raise ValueError("Fuera de la ventana de entrada")
    if inputs_time > start:
        raise ValueError("Entradas no disponibles al inicio del intervalo")
    if not 0 <= now - book_time <= limits.max_book_age_seconds:
        raise ValueError("Libro atrasado o con fecha futura")
    qty, probability, max_price, fees, min_size = [number(proposal[k]) for k in
        ('shares', 'p_win', 'max_price', 'total_costs_extra', 'min_order_size')]
    if not 0 <= probability <= 1 or qty <= 0 or min_size <= 0 or qty < min_size or fees < 0 or not 0 < max_price < 1:
        raise ValueError("Cantidad, probabilidad, precio o costos inválidos")
    asks = [(number(p), number(s)) for p, s in proposal['asks']]
    if not asks or any(not 0 < p < 1 or s <= 0 for p, s in asks):
        raise ValueError("Liquidez inválida")
    bid = number(proposal['best_bid'])
    best_ask = min(p for p, _ in asks)
    if not 0 <= bid <= best_ask or best_ask - bid > number(limits.max_spread):
        raise ValueError("Spread fuera de límite")
    remaining, gross = qty, Decimal(0)
    for price, size in sorted(asks):
        if price > max_price:
            break
        filled = min(remaining, size)
        gross += filled * price
        remaining -= filled
        if remaining == 0:
            break
    if remaining > 0:
        raise ValueError("Profundidad insuficiente dentro del precio límite")
    total = gross + fees
    # Modelo Paper hold-to-resolution: una participación ganadora paga una unidad.
    expected_net = probability * qty - total
    if limits.require_net_edge and expected_net / qty < number(limits.minimum_net_edge):
        raise ValueError(f"Ventaja estimada {expected_net / qty:.4f} USD por participación; se exige {limits.minimum_net_edge} después de costos")
    if total > min(number(cash), number(limits.max_order_cost)):
        raise ValueError("Saldo o importe máximo insuficiente")
    if number(exposure) + total > number(limits.max_total_exposure):
        raise ValueError("Límite de exposición")
    # Presupuesto conservador: pérdidas realizadas + toda la exposición abierta.
    if limits.enforce_daily_loss and number(daily_losses) + number(exposure) + total > number(limits.max_daily_loss_budget):
        raise ValueError(f"Presupuesto diario de pérdidas agotado: pérdidas {daily_losses} + exposición {exposure} + compra {total} USD supera {limits.max_daily_loss_budget} USD (día UTC)")
    return {'shares': str(qty), 'gross': str(gross), 'costs_extra': str(fees),
            'total_cost': str(total), 'expected_net': str(expected_net), 'vwap': str(gross / qty)}


class PaperLedger:
    def __init__(self, path, initial_cash):
        initial_cash = number(initial_cash)
        if initial_cash <= 0:
            raise ValueError("Capital inicial inválido")
        self.db = sqlite3.connect(path, timeout=10)
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS paper_controls(id INTEGER PRIMARY KEY CHECK(id=1),daily_loss_enabled INTEGER NOT NULL CHECK(daily_loss_enabled IN (0,1)));
            INSERT OR IGNORE INTO paper_controls VALUES(1,1);
            CREATE TABLE IF NOT EXISTS account (id INTEGER PRIMARY KEY CHECK(id=1), cash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS orders (
                market_id TEXT PRIMARY KEY, outcome TEXT NOT NULL, shares TEXT NOT NULL,
                cost TEXT NOT NULL, end REAL NOT NULL, settled_at REAL, payout TEXT, pnl TEXT);
            CREATE TABLE IF NOT EXISTS decisions (id INTEGER PRIMARY KEY, payload TEXT, result TEXT);
        ''')
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO account VALUES (1, ?)', (str(initial_cash),))

    @property
    def cash(self):
        return number(self.db.execute('SELECT cash FROM account WHERE id=1').fetchone()[0])

    def buy(self, proposal, limits):
        """Una compra por mercado, con saldo y reserva actualizados atómicamente."""
        payload = json.dumps(proposal, allow_nan=False, sort_keys=True)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            try:
                market_id = proposal['market_id']
                if self.db.execute('SELECT 1 FROM orders WHERE market_id=?', (market_id,)).fetchone():
                    raise ValueError("Mercado ya procesado")
                exposure = sum((number(r[0]) for r in self.db.execute('SELECT cost FROM orders WHERE settled_at IS NULL')), Decimal(0))
                if 'stake_fraction' in proposal:
                    capital = self.cash + exposure
                    required = (capital * Decimal('0.03')).quantize(Decimal('0.01'), rounding='ROUND_DOWN')
                    if proposal.get("stake_policy") == "three_percent_or_minimum":
                        from paper_stake import size_for_budget
                        verified = size_for_budget(proposal["fee_market"],
                            {"min_order_size": proposal["min_order_size"],
                             "asks": [dict(price=p,size=s) for p,s in proposal["asks"]]},
                            proposal["max_price"],required,allow_minimum=True)
                        required = number(verified["total_cost"]) if verified["minimum_adjusted"] else required
                        if number(proposal["shares"]) != number(verified["shares"]) or number(proposal["total_costs_extra"]) != number(verified["fees"]):
                            raise ValueError("El tamaño no coincide con el 3% o mínimo verificado")
                    if number(proposal['stake_fraction']) != Decimal('0.03') or number(proposal['stake_budget']) != required or number(proposal['capital_basis']) != capital:
                        raise ValueError("El presupuesto debe ser el 3% del capital contable actualizado")
                now = float(proposal['now'])
                day_start = datetime.fromtimestamp(now, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
                daily_losses = sum((max(-number(r[0]), Decimal(0)) for r in self.db.execute(
                    'SELECT pnl FROM orders WHERE settled_at >= ? AND settled_at <= ?', (day_start, now))), Decimal(0))
                daily_enabled = bool(self.db.execute("SELECT daily_loss_enabled FROM paper_controls WHERE id=1").fetchone()[0])
                fill = evaluate_buy(proposal, replace(limits,enforce_daily_loss=daily_enabled), self.cash, exposure, daily_losses)
                if 'stake_fraction' in proposal:
                    if not Decimal(0) <= required-number(fill['total_cost']) <= Decimal('0.000012'):
                        raise ValueError("La ejecución debe cubrir el presupuesto del 3% o mínimo validado")
                self.db.execute('INSERT INTO orders VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL)',
                    (market_id, proposal['outcome'], fill['shares'], fill['total_cost'], proposal['end']))
                self.db.execute('UPDATE account SET cash=? WHERE id=1', (str(self.cash - number(fill['total_cost'])),))
                result = {'status': 'filled_paper', **fill}
            except (ValueError, KeyError, TypeError, OverflowError) as exc:
                result = {'status': 'rejected', 'reason': str(exc)}
            self.db.execute('INSERT INTO decisions(payload,result) VALUES (?,?)', (payload, json.dumps(result)))
            self.db.commit()
            return result
        except BaseException:
            self.db.rollback()
            raise

    def settle(self, market_id, winner, resolved_at, now, final_verified=False):
        if winner not in ('Up', 'Down') or final_verified is not True:
            raise ValueError("Resolución final no verificada")
        if not math.isfinite(resolved_at) or not math.isfinite(now):
            raise ValueError("Fecha de resolución inválida")
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row = self.db.execute('SELECT outcome,shares,cost,end,settled_at FROM orders WHERE market_id=?', (market_id,)).fetchone()
            if row is None or row[4] is not None:
                raise ValueError("Orden ausente o ya liquidada")
            if not row[3] <= resolved_at <= now:
                raise ValueError("Resolución prematura o futura")
            payout = number(row[1]) if row[0] == winner else Decimal(0)
            pnl = payout - number(row[2])
            self.db.execute('UPDATE account SET cash=? WHERE id=1', (str(self.cash + payout),))
            self.db.execute('UPDATE orders SET settled_at=?, payout=?, pnl=? WHERE market_id=?', (now, str(payout), str(pnl), market_id))
            self.db.commit()
            return {'payout': str(payout), 'net_pnl': str(pnl)}
        except BaseException:
            self.db.rollback()
            raise

    def close(self):
        self.db.close()


def training_eligible(row, cutoff):
    """Solo observaciones a la apertura con resolución disponible al corte."""
    try:
        values = [float(row[k]) for k in ('inputs_available_at', 'start', 'end', 'label_available_at')]
        available, start, end, label_time = values
        return (math.isfinite(cutoff) and all(math.isfinite(v) for v in values)
                and row.get('resolution_final') is True and row.get('outcome') in ('Up', 'Down')
                and end - start == 300 and available <= start < end <= label_time <= cutoff)
    except (KeyError, TypeError, ValueError):
        return False
