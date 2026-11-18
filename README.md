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

### Análisis visual en Jupyter / VS Code (entrada principal)

Abre `Analisis_modelos_BTC.ipynb`. Incluye las seis gráficas, tablas, comparación de modelos y conclusiones dentro del archivo, con salidas ya guardadas. No necesitas ejecutar el notebook para leer los resultados actuales. Si el editor pide confiar en el archivo, revisa su origen y márcalo como confiable para visualizar las salidas HTML/SVG locales.

Para actualizarlo, selecciona el kernel `.venv` y ejecuta las celdas en orden. Si no aparece el entorno en VS Code/Jupyter, instala el soporte del kernel:

```powershell
.venv/Scripts/python.exe -m pip install ipykernel
```

El notebook contiene las funciones de visualización y usa los datos/resultados locales existentes. Ajusta `PROYECTO` si cambias la ubicación. La última celda permite repetir el entrenamiento con `REENTRENAR = True`; está desactivada por defecto y conserva el test final reservado. El script `.py` queda como alternativa para terminal, sin necesidad de ejecutarlo para visualizar el notebook.

### Ver todo el análisis con un solo script

Desde esta carpeta ejecuta:

```powershell
python analisis_modelos.py
```

Genera `data/analisis/informe_modelos.html` y lo abre en el navegador. Contiene las gráficas de acierto y RMSE de los nueve modelos, la clasificación de las 60 configuraciones, estabilidad por periodo, acierto móvil, predicciones frente al resultado, errores y matriz Down/Up. El informe incluye sus conclusiones y límites; es el punto de consulta principal del análisis. Los modelos originales y los optimizados se muestran por separado porque sus periodos de validación difieren.

El modo de consulta usa solamente la biblioteca estándar de Python y los resultados locales existentes. El HTML incluye todas las gráficas y funciona sin conexión. Para generar sin abrir el navegador: `python analisis_modelos.py --sin-abrir`. Si los datos están en otra ubicación, consulta `python analisis_modelos.py --help`.

Para repetir desde los datos auditados el entrenamiento y la optimización, conservando el test final reservado:

```powershell
.venv/Scripts/python.exe analisis_modelos.py --reentrenar
```

Este modo reutiliza los motores `training.py` y `optimize.py`; sus resultados quedan agrupados bajo `models/analisis/<ejecución>/`, excluidos de Git. Los motores se conservan para evitar duplicar código. La consulta normal no reentrena ni crea carpetas de experimentos nuevas; solo actualiza el HTML. Los informes previos se mantienen como evidencia histórica.

Análisis adicional del candidato: intervalo descriptivo bootstrap de la mejora −0.88 a +6.18 puntos porcentuales. Incluye cero, depende del supuesto de disponibilidad histórica y no corrige selección de 60 modelos. Sigue siendo candidato de investigación.

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
