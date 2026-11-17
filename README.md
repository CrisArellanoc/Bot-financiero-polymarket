# Bot-financiero-polymarket

Proyecto de predicción BTC Up/Down de cinco minutos, una predicción al inicio de cada intervalo, con objetivo de operación autónoma. La primera etapa operativa será simulada (Paper).

## Estado al 17 de noviembre de 2026

- REST y WebSocket públicos verificados; 26,163 eventos guardados en SQLite.
- 2,015 mercados históricos resueltos auditados.
- Nueve modelos del notebook y 60 configuraciones de optimización entrenados para investigación.
- Mejor promedio exploratorio: 54.64%, frente a 51.99% de referencia, sin mejora consistente en los tres periodos.
- 303 ejemplos finales siguen reservados. Ningún modelo está aprobado para comprar.
- Componentes Paper probados; integración autónoma continua, calibración y reentrenamiento programado pendientes.

El protocolo DL y la meta de +20% fueron retirados por el usuario. El notebook se conserva como referencia inicial de modelos y métricas; el usuario autorizó optimizar ventanas, variables e hiperparámetros.

## Documentación

- [Resultados, evidencia y límites de esta etapa](docs/RESULTADOS_ETAPA_2.md)
- [Diseño inicial del notebook](docs/INICIO.md)
- [Autonomía y reentrenamiento: diseño y pendientes](docs/AUTONOMIA.md)
- [Configuración vigente](config/experiments.json)

## Entorno y pruebas

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-lock.txt
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

REST y componentes Paper usan biblioteca estándar; entrenamiento requiere las dependencias del archivo de versiones. El capturador WebSocket requiere Node.js 22+.

## Datos públicos

```powershell
.venv/Scripts/python.exe polymarket_public.py --output data/current_snapshot.json
node collect_market.mjs data/current_snapshot.json data/stream_new.jsonl 30
.venv/Scripts/python.exe market_history.py --intervals 2016 --output data/history_new
.venv/Scripts/python.exe event_store.py --db data/market_data.sqlite --snapshot data/current_snapshot.json --stream data/stream_new.jsonl --history data/history_new/records.jsonl
```

Usar archivos/carpetas de captura e historial nuevos. El WebSocket captura un mercado durante un plazo limitado; no es todavía el colector continuo del bot.

## Reproducir investigación histórica

```powershell
.venv/Scripts/python.exe training.py --records data/history_7d_20261117/records.jsonl --output models/research_new --allow-reported-close-proxy
.venv/Scripts/python.exe optimize.py --records data/history_7d_20261117/records.jsonl --output models/optimization_new
```

`training.py` usa disponibilidad observada por defecto. El flag habilita el supuesto histórico de cierre reportado; `optimize.py` está explícitamente dedicado a esa investigación histórica. Los artefactos se marcan no aptos para operar. Ninguno de esos comandos evalúa el test final ni promueve modelos.

Los datos, modelos, claves y entorno virtual están excluidos de Git. No hay rutas de envío de órdenes reales ni procesos continuos activos. La API del LLM local sigue pendiente del usuario.
