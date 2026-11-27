"""Importe Paper del 3% del capital contable y tamaño ejecutable con comisiones."""
from decimal import Decimal, ROUND_DOWN, ROUND_UP

from autonomy import number

FRACTION = Decimal("0.03")
CENT = Decimal("0.01")
SHARE_STEP = Decimal("0.000001")
FEE_STEP = Decimal("0.00001")


def capital_and_stake(db):
    """Capital = efectivo + costo de posiciones pendientes, sin revaloración."""
    account = db.execute("SELECT cash FROM account WHERE id=1").fetchone()
    if account is None:
        raise ValueError("No existe la cuenta simulada")
    cash = number(account[0])
    costs = [number(row[0]) for row in db.execute(
        "SELECT cost FROM orders WHERE settled_at IS NULL")]
    if cash < 0 or any(cost <= 0 for cost in costs):
        raise ValueError("Capital contable inválido")
    capital = cash + sum(costs, Decimal(0))
    return capital, (capital * FRACTION).quantize(CENT, rounding=ROUND_DOWN)


def fee_rate(raw):
    rate = Decimal(0)
    if raw.get("feesEnabled") is not False:
        schedule = raw.get("feeSchedule")
        if (raw.get("feesEnabled") is not True or not isinstance(schedule, dict)
                or type(schedule.get("exponent")) not in (int, float)
                or schedule["exponent"] != 1 or schedule.get("takerOnly") is not True):
            raise ValueError("Comisión no verificada para este mercado")
        rate = number(schedule["rate"])
        if not 0 < rate < 1:
            raise ValueError("Tasa taker inválida")
    return rate


def size_for_budget(raw, book, limit, budget):
    """Invierte el presupuesto completo al centavo, sin excederlo ni reducirlo.

    Redondea participaciones hacia abajo a seis decimales y comisión total hacia
    arriba a cinco. Un residuo inferior a un centavo es solo redondeo de tamaño;
    la falta de profundidad, los mínimos o una tarifa desconocida rechazan el
    intento en lugar de invertir un importe menor o aumentar el 3%.
    """
    limit, budget = number(limit), number(budget)
    if not 0 < limit < 1 or budget <= 0 or budget != budget.quantize(CENT):
        raise ValueError("Presupuesto o precio límite inválido")
    minimum = max(number(book["min_order_size"]), number(raw["orderMinSize"]))
    if number(book["min_order_size"]) <= 0 or number(raw["orderMinSize"]) <= 0:
        raise ValueError("Mínimo de participaciones no verificado")
    rate = fee_rate(raw)
    levels = sorted((number(row["price"]), number(row["size"])) for row in book["asks"])
    if not levels or any(not 0 < price < 1 or size <= 0 for price, size in levels):
        raise ValueError("Profundidad de compra inválida")
    levels = [(price, size) for price, size in levels if price <= limit]
    if not levels:
        raise ValueError("No hay liquidez dentro del precio límite")

    def quote(micros):
        quantity = Decimal(micros) * SHARE_STEP
        remaining, gross, fees = quantity, Decimal(0), Decimal(0)
        for price, size in levels:
            amount = min(remaining, size)
            gross += amount * price
            fees += amount * rate * price * (1-price)
            remaining -= amount
            if remaining == 0:
                break
        if remaining:
            raise ValueError("Profundidad insuficiente")
        fees = fees.quantize(FEE_STEP, rounding=ROUND_UP)
        return gross, fees

    depth = sum((size for _, size in levels), Decimal(0))
    high = int((min(depth, budget/levels[0][0])/SHARE_STEP).to_integral_value(rounding=ROUND_DOWN))
    low = 0
    while low < high:
        middle = (low+high+1)//2
        gross, fees = quote(middle)
        if gross+fees <= budget:
            low = middle
        else:
            high = middle-1
    quantity = Decimal(low) * SHARE_STEP
    if quantity < minimum:
        raise ValueError("El 3% no alcanza el mínimo de participaciones del mercado")
    gross, fees = quote(low)
    total = gross+fees
    # Solo tolerar el residuo inevitable de una microunidad de participaciones
    # y del redondeo de comisión; no un llenado parcial por falta de profundidad.
    if total <= 0 or budget-total > FEE_STEP+2*SHARE_STEP:
        raise ValueError("No hay liquidez para invertir el 3% completo")
    return {"shares": str(quantity), "fees": str(fees), "total_cost": str(total),
            "stake_budget": str(budget), "gross": str(gross)}

def sale_quote(raw, book, shares):
    """Proceeds for selling the entire position into current bids after fees."""
    remaining = number(shares)
    if remaining <= 0 or remaining < max(number(raw["orderMinSize"]), number(book["min_order_size"])):
        raise ValueError("La posición no alcanza el mínimo de venta")
    rate = fee_rate(raw)
    gross = fees = Decimal(0)
    levels = sorted(((number(row["price"]), number(row["size"])) for row in book["bids"]), reverse=True)
    for price, size in levels:
        if not 0 < price < 1 or size <= 0:
            raise ValueError("Oferta de compra inválida")
        quantity = min(remaining, size)
        gross += quantity * price
        fees += quantity * rate * price * (1-price)
        remaining -= quantity
        if not remaining:
            return gross - fees.quantize(FEE_STEP, rounding=ROUND_UP)
    raise ValueError("Profundidad insuficiente para vender la posición completa")
