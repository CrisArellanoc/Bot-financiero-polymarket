# Bot-financiero-polymarket

Proyecto de predicción BTC Up/Down de cinco minutos, una predicción al inicio de cada intervalo, con objetivo de operación autónoma. La primera etapa operativa será simulada (Paper).

## Monitor en vivo y simulación

Desde la raíz del repositorio, con Python 3.12 y las dependencias de `requirements-lock.txt` en el entorno activo, ejecuta:

```powershell
python live_paper.py
```

Abre [el monitor local](http://127.0.0.1:8765). Déjalo corriendo para acumular intervalos y detén el proceso con Ctrl+C. Guarda cotizaciones, mensajes, pronósticos, etiquetas, modelos y cartera en `data/live_paper.sqlite`. Al reiniciar vuelve a la misma cartera. El selector permite explorar un intervalo anterior y desplegar los datos de cada mensaje. El notebook sigue siendo la entrada para el análisis histórico.

### Laya experimental y gráfica del mercado activo

El modo ligero carga un único predictor Laya en CPU y limita PyTorch a un hilo de cálculo. Para preparar el entorno por primera vez, desde la raíz:

```powershell
python -m pip install laya==0.5.0
python laya_shadow.py
```

La descarga prepara el checkpoint inglés de Laya en `models/laya-en`. En cada apertura, Laya lee las cinco variables previamente congeladas y responde una pregunta binaria. Su puntuación para Up es experimental y queda fuera de las decisiones de compra. El panel compara acierto con el libro y el MLP usando las resoluciones de los mismos intervalos.

La gráfica usa el contrato Up del intervalo activo y toma muestras del CLOB aproximadamente cada cinco segundos. Muestra el punto medio bid/ask o, si el spread supera 0.10, la última operación. La segunda línea comienza cuando está disponible la predicción y queda fija. Las dos series usan escala 0–100%; el precio spot BTC/USD se mide en otras unidades. Las ventanas antiguas sin muestras conservan sus datos originales. Si faltan pesos o falla la inferencia, el monitor lo indica.

### Supervisor opcional con Ollama

Para activar el supervisor adicional, ejecuta `python live_paper.py --modelo-local qwen3.5:4b` con Ollama abierto. El modelo revisa los mensajes del agente, clasifica su estado y selecciona los hechos que destaca en la sección **Supervisor de IA local**. Cada importe, dirección y motivo visible procede del evento registrado. Puedes contrastar el informe con los mensajes debajo de la gráfica.

La inferencia utiliza exclusivamente `127.0.0.1:11434` y pesos instalados en el equipo. El proceso descarta modelos remotos, bloquea redirecciones y prescinde de claves, descargas automáticas y APIs de IA de pago. Polymarket sigue usando Internet para proporcionar datos públicos. Si Ollama está apagado, el panel muestra el error y vuelve a intentar tras 30 segundos. La recopilación y el control de riesgo se ejecutan independientemente. El supervisor agrupa los eventos recientes de cada intervalo y atiende primero los más nuevos tras una pausa. Los informes terminados se guardan en disco. [API local de Ollama](https://docs.ollama.com/api/chat)

Para elegir otro modelo de texto ya instalado, comprueba su nombre con `ollama list` y ejecuta `python live_paper.py --modelo-local NOMBRE`.

### Señal, entrenamiento y compras

El agente consulta por REST el libro Up del **próximo** mercado cada 4–10 segundos. En la apertura congela las últimas lecturas recibidas antes del corte y calcula precio medio, spread, desequilibrio de tamaños, cambio del precio medio y su volatilidad. Requiere dos observaciones separadas por al menos cinco segundos y una cotización final de hasta 24 segundos. Una apertura perdida queda registrada como abstención. El resultado se confirma después por Gamma contrastando pago binario, estado final, precio de referencia y precio final.

Durante la recopilación inicial, el pronóstico de referencia es el punto medio del libro Up. Al llegar a **288 pronósticos con etiqueta oficial** se ajusta un perceptrón multicapa (MLP) con ocho neuronas ocultas. Cada 48 etiquetas nuevas vuelve a ajustar con hasta 2,016 ejemplos prospectivos. Entrenamiento, calibración sigmoidal y validación ocupan el 55%, 20% y 25% en orden temporal. Se purgan etiquetas que aún no eran públicas en el corte siguiente. Los 303 ejemplos históricos reservados pertenecen al experimento anterior.

Para habilitar **compras experimentales simuladas**, el último MLP debe superar al punto medio del libro en acierto y en Brier (menor es mejor) en cada mitad de la validación. Una versión con más de siete días deja de ser elegible. El agente elige Up o Down por probabilidad calibrada y pide el libro ejecutable de ese lado. La cartera verifica las reglas del mercado, la antigüedad de la cotización, el mínimo de orden, la profundidad, el spread, las comisiones y la ventaja esperada. Los costos se calculan por nivel con la tarifa publicada para ese mercado. Los fills representan la liquidez mostrada en la consulta y pueden diferir de una ejecución real. [Mercados y restricciones](https://docs.polymarket.com/market-data/market-details) · [Comisiones](https://docs.polymarket.com/trading/fees)

La cartera de demostración empieza con 100 USD virtuales. Cada intento es de 20 participaciones, con un costo máximo de 15 USD, exposición de hasta 30 USD y presupuesto diario de pérdida de 35 USD (UTC). Exige un margen neto de 0.03 USD por participación, spread máximo de 0.05 USD, libro de hasta 12 segundos y entrada en los primeros 15 segundos. Son parámetros del experimento. El tablero distingue el origen de cada pronóstico y muestra el acierto del MLP y del libro sobre los mismos mercados resueltos, junto al beneficio o pérdida realizada.

### Relación con los capítulos de Kaabar

| Capítulo | Aplicación en este proyecto |
| --- | --- |
| 7 · Modelos clásicos | El notebook compara los nueve regresores históricos y conserva las métricas direccionales y de error. |
| 8 · Redes para series temporales | El MLP regularizado aprende la dirección a partir de la ventana previa a la apertura. |
| 9 · Validación y reentrenamiento | Cortes temporales, calibración separada, entrenamiento por lotes de etiquetas maduras y umbral de entrada después de costos. |
| 10 · Aprendizaje por refuerzo | Se registran acción, abstención y recompensa realizada para estudiar decisiones secuenciales. La política de esta versión usa reglas deterministas. Un agente de RL sigue siendo investigación futura. |
| 11 · Variables y visualización | El monitor muestra cambios, volatilidad del precio implícito del contrato y mensajes entre el libro, el modelo, el control de riesgo y la cartera. |

El umbral de elegibilidad y los parámetros de simulación son decisiones de diseño de este proyecto. [Índice de O’Reilly](https://www.oreilly.com/library/view/deep-learning-for/9781098148386/) · [Código oficial de Sofien Kaabar](https://github.com/sofienkaabar/deep-learning-for-finance)

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

Los datos, modelos, informes del supervisor y entorno virtual se conservan en el equipo y están excluidos de Git.
