# Bot BTC / Polymarket: LLM remoto y simulación

El cerebro del bot es el LLM que se consulta mediante la API del profesor. Decide Up, Down o abstención al abrir cada mercado BTC de cinco minutos. El equipo local captura datos públicos, prepara indicadores deterministas, comprueba límites, mantiene la cartera simulada y muestra la interfaz. El arranque normal no entrena ni ejecuta los MLP históricos y no carga modelos locales.

## Arranque en PowerShell

Desde la carpeta que contiene `live_paper.py`:

```powershell
git pull --ff-only
.\.venv\Scripts\python.exe -m pip install websocket-client==1.9.2
.\.venv\Scripts\python.exe live_paper.py --verificar-conexion
.\.venv\Scripts\python.exe live_paper.py
```

Abre [el monitor local](http://127.0.0.1:8765/). Deja la terminal abierta. Ctrl+C detiene el programa; cerrar el navegador no lo detiene. Usa Ctrl+F5 tras actualizar el código. La cartera y su historial se conservan al reiniciar. Con el equipo apagado o el proceso cerrado no se capturan datos.

En una instalación nueva, clona [el repositorio](https://github.com/CrisArellanoc/Bot-financiero-polymarket), entra a su carpeta y crea el entorno con `py -m venv .venv` antes de instalar la dependencia. Se requiere Python 3.12 o superior y Git. Los componentes activos usan biblioteca estándar y el cliente WebSocket; las bibliotecas de aprendizaje automático de `requirements-lock.txt` se necesitan para reproducir la investigación histórica.

## API privada del profesor

Solo debes configurar una vez tu archivo privado:

```powershell
if (!(Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
notepad .env
```

El cerebro reutiliza `SUPERVISOR_API_URL`, `SUPERVISOR_MODEL` y `SUPERVISOR_API_KEY`, o la clave común `MODEL_API_KEY`. No necesita una clave nueva. `SUPERVISOR_API_URL` apunta a la ruta HTTPS completa de chat completions del servidor. `SUPERVISOR_MODEL` vacío descubre el identificador en `/v1/models`. Las llamadas del cerebro y del supervisor son independientes.

Laya usa `LAYA_API_URL`, `LAYA_API_FORMAT=workspace` y la clave común o `LAYA_API_KEY`. Es un observador remoto adicional; no sustituye la decisión del LLM ni los límites de la cartera. Se conserva el supervisor remoto para revisar los eventos. `--verificar-conexion` prueba las tres funciones con datos de ejemplo sin apostar.

Conéctate a la red del profesor o a una VPN autorizada. El cliente verifica TLS y no reenvía claves en redirecciones. Si el servidor tiene una CA privada, configura `MODEL_CA_FILE` con la ruta al certificado PEM público de confianza entregado por su administrador. Las claves no se muestran en el panel, la auditoría ni los registros. `.env`, `data/`, `models/` y `.venv/` están excluidos de Git. Nunca pongas claves de Polymarket o billeteras en las variables del profesor.

## Decisión y ejecución simulada

1. Se reciben el libro del mercado próximo, el precio a superar oficial y el TWAP de BTC de Polymarket. Al abrir se conservan entradas disponibles antes del corte y la referencia publicada al inicio. Si la referencia aún no llegó se espera dentro del plazo inicial.
2. Se envían al LLM los indicadores del libro, el TWAP, la referencia, el presupuesto y hasta ocho resultados oficiales previos. Los datos congelados y su hash se guardan antes de llamar a la API.
3. El LLM responde JSON con acción, puntuación Up, justificación breve y referencias de evidencia. La respuesta se valida incluso si el servidor ignora el esquema. No se publica razonamiento interno. Su puntuación es subjetiva y no calibrada; el historial como contexto no cambia sus pesos ni constituye entrenamiento.
4. Up/Down pasa a controles deterministas: ventana inicial de 15 segundos, estado y reglas del mercado, antigüedad del libro hasta 12 segundos, spread hasta 0.05, liquidez, mínimo de participaciones y margen estimado después de comisiones de 0.03 por participación. Una respuesta tardía, inválida o ausente produce rechazo. Abstenerse es una decisión válida.
5. Solo una compra simulada por mercado se registra con efectivo reservado. La liquidación espera un resultado oficial final de Gamma. Se registran costo, pago y ganancia o pérdida neta y se incorpora el resultado al contexto de consultas posteriores.

La simulación es experimental. Usar un LLM como cerebro no demuestra ventaja predictiva ni rentabilidad. No hay órdenes reales ni conexión autenticada de trading. La API de Polymarket se usa para lectura pública.

## Apuesta fija del 3% del capital total

Capital contable = efectivo disponible + costo de posiciones pendientes. Equivale al capital inicial más resultados realizados; no incluye revalorización de posiciones abiertas. La cuenta nueva comienza con 100 USD virtuales. Una cuenta existente nunca se reinicia al actualizar.

Presupuesto = 3% del capital contable, redondeado hacia abajo al centavo. Con 100 USD corresponde a 3.00 USD; con 90 USD a 2.70 USD y con 103 USD a 3.09 USD. Las comisiones quedan dentro de ese importe. Se admite únicamente el residuo de redondear participaciones a seis decimales y comisión a cinco (como máximo 0.000012 USD), sin exceder el presupuesto.

No se sube ni reduce la apuesta para alcanzar mínimos: si el 3% no alcanza el mínimo de participaciones o la profundidad no permite cubrirlo completo, se rechaza y se explica. También se mantienen límites experimentales de exposición total de 30 USD y presupuesto diario de pérdida de 35 USD (UTC). Si el efectivo o los controles no alcanzan, no se apuesta. El 3% se vuelve a verificar dentro de la transacción de la cartera para evitar presupuestos desactualizados. La comprobación del margen utiliza la puntuación subjetiva del LLM, no una garantía económica.

La fórmula de comisiones se verifica con los parámetros publicados de cada mercado; tarifas desconocidas bloquean el intento. Referencias: [comisiones](https://docs.polymarket.com/trading/fees), [mercados](https://docs.polymarket.com/market-data/market-details).

## Panel, neuronas y pestaña lateral

La gráfica BTC en USD usa el TWAP de Chainlink de 60 segundos distribuido por Polymarket y su precio a superar. La puntuación Up del LLM y la de Laya se dibujan en una escala porcentual del contrato, separada de los dólares. La antigua regresión local no se dibuja como predicción activa en este modo.

La red muestra Polymarket, libro, LLM remoto, riesgo, cartera, resultado oficial y evaluación. Cada neurona representa un módulo con soma, núcleo, dendritas y axón. Los impulsos corresponden a mensajes registrados; no son activaciones internas de la red del proveedor. Selecciona nodos o mensajes para consultar datos y decisiones. La reproducción histórica está identificada y respeta movimiento reducido.

La pestaña lateral de apuestas se abre al recibir un intento nuevo o una actualización de su estado. Distingue preparando/pendiente, ejecutada en simulación, rechazada y abstención. Muestra capital, 3% objetivo, costo confirmado, lado, modelo, motivo y hashes. Una decisión Up no se presenta como compra ejecutada: la confirmación procede del recibo de la cartera.

El historial conserva costo, pago, resultado neto y capital realizado. Victoria/pérdida se refiere al resultado neto después de costos. Las operaciones pendientes no cuentan como victorias. La interfaz muestra las últimas 200 apuestas e intentos y totales completos. Los eventos y recibos completos permanecen en SQLite. La viñeta del profesor muestra respuestas remotas recientes y los errores por servicio; no certifica eficacia predictiva.

## Historial auditable

Cada intento tiene enlaces a `/api/audit?start=APERTURA_UNIX`. También puedes abrir [la exportación completa](http://127.0.0.1:8765/api/audit) y guardarla como JSON desde el navegador. Incluye evidencia congelada, respuesta validada, propuesta, confirmación o rechazo, orden y resolución. Los importes contables se conservan como texto decimal.

`audit_events` encadena recibos SHA-256; `chain_verified`, `decision_hashes_verified` y `receipts_verified` revisan su integridad y la concordancia con los registros de decisión y cartera. Esta verificación local detecta modificaciones, pero no es una firma externa ni impide que alguien con control completo de la base reescriba toda la cadena. Guarda exportaciones por separado si necesitas un testigo externo. Los registros anteriores a esta adaptación se conservan, pero no se inventan recibos retrospectivos (`audit_coverage=no_remote_receipt`).

Un reinicio no repite consultas o apuestas de un mismo intervalo. Si se interrumpió una consulta, queda rechazada; si la compra ya estaba confirmada, se recupera su recibo. Las apuestas se mantienen hasta resolución en esta simulación.

## Datos en vivo

Gamma y CLOB se consultan para mercados, libros y resultados. El precio a superar se obtiene de `eventMetadata.priceToBeat` del evento Gamma cuyo slug coincide exactamente con el intervalo. Se contrasta de nuevo durante el mercado. Mientras Gamma no lo publique, se permite únicamente el TWAP de 60 segundos con timestamp exactamente igual a la apertura; se identifica como pendiente de confirmación. No se aproxima con otro tick ni con el primer precio posterior. Si falta, se muestra pendiente y el bot se abstiene.

Se corrigió el uso anterior de `openPrice` del endpoint web `crypto-price`: la comprobación directa mostró que puede diferir del `priceToBeat` de Gamma. Los valores antiguos de esa fuente no se muestran como referencias válidas. `btc_target_revisions` conserva los cambios de referencia y su fuente; la evidencia enviada al LLM permanece congelada. Si la referencia cambia durante la consulta, se rechaza el intento. La interfaz enlaza el mismo intervalo en Polymarket para verificar que se compara el mercado correcto. Los intervalos antiguos sin referencia corregida quedan pendientes, sin inventar historial.

El TWAP usa `wss://ws-live-data.polymarket.com`, tema `crypto_prices_twap_sixty`, filtro compacto `{"symbol":"btc/usd"}` y ventana de 60 segundos. Se validan símbolo, tiempos, precisión E18 y frescura. Una conexión recibe actualizaciones, envía latidos cada cinco segundos y reconecta con esperas hasta 30 segundos. La gráfica consulta cada dos segundos. Si falla, informa y deja huecos; no cambia a Yahoo.

RTDS público se verificó en esta etapa, pero Polymarket ha anunciado su [migración a PolyBolt](https://docs.polymarket.com/migrate/rtds-to-polybolt.md), que requiere autenticación adicional. Los datos de Yahoo y sus modelos quedan como investigación previa y no se descargan ni ejecutan en el arranque operativo actual.

## Investigación anterior

`Analisis_modelos_BTC.ipynb` conserva las gráficas y métricas de nueve modelos y 60 configuraciones iniciales. Los modelos, capturas y reportes locales permiten reproducir esos experimentos con `training.py`, `optimize.py` y `yahoo_predictor.py`, instalando `requirements-lock.txt`. No se ejecutan automáticamente en el modo LLM.

El mejor promedio exploratorio inicial fue 54.64% frente a 51.99%, sin mejora estable en todos los periodos. El MLP de Yahoo obtuvo 51.80% sobre 3,448 velas históricas, que no demuestra rentabilidad en Polymarket. Los 303 ejemplos finales del experimento inicial siguen reservados. Los reportes antiguos describen su versión y no deben interpretarse como estado operativo actual. Referencia: [resultados de la etapa inicial](docs/RESULTADOS_ETAPA_2.md).


## Evaluación prospectiva del LLM

El panel incorpora un selector por modelo remoto y versión del prompt. Las consultas nuevas guardan una versión y huella del prompt, el inicio y fin de la consulta y si su respuesta pasó la validación. Se incluyen en la auditoría. Los registros previos conservan versión o duración desconocida; no se rellenan con cifras inventadas. Los fallos de API e interrupciones se registran aparte de abstenciones y rechazos por riesgo. La instrumentación no modifica las instrucciones ni entrena el LLM.

- **Predicciones:** Brier, log loss, Brier Skill Score frente al libro, matriz de confusión, acierto por dirección, precisión, sensibilidad, F1 y acierto balanceado. Brier y calibración incluyen abstenciones que tengan puntuación numérica; la matriz usa solo Up/Down. Solo se evalúan respuestas válidas y emitidas en los primeros 15 segundos, con entradas previas a abrir y etiqueta oficial publicada. Las respuestas tardías siguen en el historial, excluidas de esas métricas.
- **Comparaciones:** Brier Skill = `1 - Brier_LLM / Brier_libro`, usando exactamente los mismos mercados. Una referencia perfecta con denominador cero produce «sin medir», no infinito. El libro es el punto medio Up congelado en la evidencia, no una cotización posterior. La referencia histórica estima frecuencia Up con resultados ya publicados antes de cada apertura y suavizado `(Up+1)/(N+2)`; no usa la mayoría del periodo evaluado.
- **Calibración:** gráfico de fiabilidad en diez deciles, frecuencia Up observada, tamaño de cada grupo y ECE (promedio ponderado del error absoluto de cada decil). Esto describe calibración, no calibra ni convierte la puntuación subjetiva en una probabilidad validada. ECE depende del tamaño de muestra y de la partición.
- **Cobertura:** señales Up/Down válidas a tiempo divididas entre intervalos registrados. Se muestran abstenciones, fallos de API, respuestas fuera de plazo, rechazos de riesgo y estados de la cartera. No se supone que el bot observó todos los mercados mientras estuvo apagado.
- **Apuestas:** resultado neto medio y acumulado después de costos, profit factor (ganancias netas positivas / pérdidas netas absolutas), racha máxima de pérdidas y pendiente/resuelta. Sin pérdidas el profit factor no se define; no se presenta como éxito infinito. El resultado esperado medio usa la puntuación subjetiva del LLM y se identifica como tal. Los totales de cada versión solo incluyen órdenes vinculadas a sus intentos remotos.
- **Cartera global:** curva de capital realizado y drawdown máximo, en USD y porcentaje, incluyendo los registros anteriores. La curva se actualiza por liquidación; no valora posiciones pendientes al precio de mercado ni representa un drawdown intramercado. Todas las liquidaciones se usan para calcular el máximo; se pueden compactar los puntos de la gráfica.
- **Estabilidad:** tablas por día y hora UTC de apertura, y por volatilidad previa del contrato Up. Los umbrales fijos son 0.005 y 0.02 de desviación de cotizaciones, no volatilidad BTC en dólares. Se muestran los tamaños de muestra comparables de cada segmento.
- **Incertidumbre:** intervalo descriptivo del 95% para la mejora absoluta de Brier (libro menos LLM) y el resultado medio por apuesta, mediante 300 réplicas bootstrap de días completos con semilla fija. Con menos de dos días se informa que falta información. El remuestreo conserva dependencia intradía, no garantiza independencia entre días ni corrige haber seleccionado la mejor versión.
- **Operación:** latencia mediana y percentil 95 de consultas terminadas, tasa de respuesta válida y tasa dentro de plazo, con denominadores explícitos. La latencia incluye descubrimiento de modelo cuando hace falta. Las consultas interrumpidas y las duraciones antiguas desconocidas no se inventan ni se cuentan como latencia cero.

El resumen conjunto puede mezclar versiones y no prueba que una sea mejor que otra: selecciónalas por separado y evalúa cambios en intervalos posteriores sin ajustar umbrales con esos mismos resultados. La instrumentación muestra evidencia para tomar esa decisión, sin optimizar automáticamente prompts ni criterios de apuesta. La evaluación utiliza biblioteca estándar y reutiliza cálculos cuando solo cambian cotizaciones, para reducir carga local. No cambia el porcentaje de apuesta ni pide consultas adicionales al servidor del profesor.

Los resultados están disponibles también en `evaluation` dentro de `/api/state`. Referencias metodológicas: capítulos 7 a 10 de [Deep Learning for Finance](https://www.oreilly.com/library/view/deep-learning-for/9781098148386/) y [calibración probabilística](https://scikit-learn.org/stable/modules/calibration.html). La estrategia actual no es un agente entrenado por refuerzo.

## Pruebas

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
node tests/test_market_chart.cjs
node tests/test_bet_panel.cjs
node tests/test_evaluation_ui.cjs
```

Las pruebas cubren contrato JSON remoto, ausencia de inferencia local en el flujo activo, decisión tardía, fallos de API, 3% con comisiones y mínimos, idempotencia, liquidación, integridad del historial y confirmación lateral. Node.js solo se requiere para las pruebas de interfaz y el capturador histórico alternativo, no para iniciar el monitor.

### Muestreo de pronósticos con una cartera separada

Ejecuta `.\.venv\Scripts\python.exe live_paper.py --exploracion` para solicitar una dirección al LLM en cada apertura con datos suficientes. En un empate exacto se registra Up con probabilidad 0.50. El bot intenta una compra simulada al mejor precio vendedor observado aunque la ventaja estimada sea baja o negativa. Aplica los mismos controles de plazo, liquidez, comisiones, saldo y riesgo. Conserva las revisiones de salida. La cartera experimental inicia con 100 USD virtuales y persiste en `data/live_paper_exploracion.sqlite`. El encabezado del monitor identifica este modo. Evalúa los aciertos sobre pronósticos resueltos y el resultado neto sobre compras ejecutadas. Para retomar la cartera habitual omite `--exploracion`.

### Aprendizaje con el acceso de chat

Cada apertura consulta únicamente resultados oficiales disponibles a esa hora en la misma cartera. Cuando hay al menos 24 casos utilizables, comienza una comparación prospectiva. En cada pareja de intervalos UTC de cinco minutos se asigna uno a cada variante, en orden predeterminado por hash. Los intervalos perdidos pueden dejar parejas incompletas. La variante control conserva el contexto habitual. La variante memoria añade cinco aperturas similares entre las últimas 256 resueltas, según precio medio Up, spread, desequilibrio, impulso y volatilidad del libro. Se conserva la evidencia exacta en la auditoría. Esto es aprendizaje mediante ejemplos en contexto con una sola llamada al chat por apertura. Los pesos del servidor solo podrían entrenarse con acceso específico del profesor.

En Evaluación prospectiva del LLM, compara las versiones del mismo modo y modelo sobre las nuevas aperturas. Observa aciertos, número de pronósticos resueltos, cobertura y Brier (menor es mejor). Los rechazos por plazo y las abstenciones también importan. El historial anterior sirve para recuperar ejemplos. La evaluación de la nueva variante empieza después de instalar esta versión.
