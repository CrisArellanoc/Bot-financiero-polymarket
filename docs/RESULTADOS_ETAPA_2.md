# Cierre de etapa: conexión, dataset y experimentos iniciales

Fecha: 17 de noviembre de 2026. Proyecto: Bot-financiero-polymarket.

**Conclusión:** hay infraestructura de lectura pública, componentes de simulación y modelos de investigación entrenados. Aún no hay evidencia suficiente de una ventaja estable ni de rentabilidad. No existe un modelo aprobado para compras autónomas.

## Qué quedó realizado

| Componente | Evidencia | Estado |
|---|---|---|
| API REST pública | Gamma y CLOB respondieron con mercado BTC 5 m y libros Up/Down | Verificado |
| WebSocket público | Captura acotada de 30 segundos; 26,172 frames | Verificado para un mercado |
| Almacenamiento | 26,163 eventos únicos guardados en SQLite; 9 de otros mercados excluidos y 1 duplicado eliminado | Verificado |
| Historial | 2,016 mercados solicitados; 2,015 válidos y 1 excluido por referencias ausentes | Auditado |
| Modelos literales | Nueve modelos del notebook entrenados con configuración registrada | Investigación |
| Optimización | 60 configuraciones, tres bloques temporales de validación | Investigación |
| Simulación | Compras, saldo, exposición, costos, liquidación y persistencia | Pruebas con datos sintéticos |
| Operación autónoma integrada | Falta integrar captura continua, pronóstico, calibración y ejecución | Pendiente |
| Reentrenamiento automático | Hay entrenadores ejecutables y filtro temporal; falta planificador y promoción/reversión | Pendiente |

El usuario autorizó optimizar variables y parámetros, manteniendo las métricas y el test final reservado. El protocolo DL y la meta de +20% siguen retirados. La primera etapa operativa será Paper; los límites de dinero real no están definidos.

## Datos y adaptación del objetivo

Se recolectaron mercados entre 2026-11-10T20:20:00+00:00 y 2026-11-17T20:10:00+00:00 (UTC). Hay 945 resultados Up y 1070 Down, sin huecos internos entre los registros válidos.

Gamma proporcionó, en mercados cerrados, `eventMetadata.priceToBeat`, `eventMetadata.finalPrice`, estado de resolución y pagos finales. Se comprobó que el signo de finalPrice-priceToBeat coincide con el resultado oficial; la igualdad corresponde a Up. Los campos de metadata se verificaron empíricamente y deben seguir auditándose; no se presupone su disponibilidad universal.

El objetivo de regresión se adaptó a **finalPrice - priceToBeat del mercado a pronosticar**, en USD. Las entradas iniciales son diferencias de referencias `priceToBeat` de mercados anteriores ya disponibles según la hipótesis temporal. Así se conserva el uso de diferencias absolutas del notebook y se alinea la etiqueta con el contrato; no se usa una concatenación de precios de participaciones como si fuera BTC.

**Limitación temporal:** el historial se descargó ahora. `closedTime` es el cierre reportado, no una prueba de cuándo se publicaron los precios de referencia. Estos experimentos usan ese campo como aproximación explícita. El modo estricto exige fechas de primera observación y rechaza el backfill cuando no puede construir ventanas válidas. Por ello todos los pesos quedan marcados como investigación, sin autorización para operar.

## Separación temporal

Con 500 rezagos y las restricciones de disponibilidad se generaron 1,513 ejemplos. El 20% final son 303 ejemplos y sigue sin evaluarse. Dentro del 80% de desarrollo se hicieron selección y validación; se eliminaron ejemplos de entrenamiento cuyas etiquetas no estarían disponibles al comenzar la validación.

La prueba literal utilizó 966 ejemplos para ajuste y 242 para validación interna. La optimización utilizó tres bloques de 302 ejemplos cada uno, siempre posteriores a su entrenamiento. Las cifras de ambos experimentos no son directamente comparables entre sí porque sus periodos de validación difieren.

## Resultados con parámetros literales

| Modelo | Acierto Up/Down en validación | RMSE (USD) |
|---|---:|---:|
| dummy_mean | 51.65% | 117.89 |
| linear | 48.76% | 190.76 |
| svr | 51.65% | 117.84 |
| sgd | 49.59% | 192.87 |
| knn | 47.93% | 126.70 |
| decision_tree | 45.45% | 199.98 |
| random_forest | 47.11% | 127.91 |
| adaboost | 50.00% | 123.20 |
| xgboost | 50.41% | 142.05 |

Referencia mayoritaria: **51.65%** en esas 242 observaciones. Ningún modelo superó esa referencia en acierto direccional. SVR mejoró ligeramente el RMSE frente a Dummy y produjo señales siempre Down. SGD registró advertencia de falta de convergencia con las 50 iteraciones originales; se conservó el aviso.

## Optimización autorizada

Se compararon 10, 20 y 50 rezagos; versiones con y sin estadísticas móviles e información horaria UTC; y configuraciones de regresión lineal, SVR, KNN, Random Forest y XGBoost. Las variables adicionales se calcularon únicamente a partir de la ventana histórica de cada muestra. El espacio de búsqueda y todas las predicciones de validación quedaron guardados.

Mejor promedio: **54.64%** con `xgb_d2_lag50_aug`. Referencia media en los mismos bloques: **51.99%**. Diferencia: **2.65 puntos porcentuales**.

| Bloque temporal | Observaciones | Candidato | Referencia | Diferencia |
|---|---:|---:|---:|---:|
| 1 | 302 | 52.32% | 52.98% | -0.66 pp |
| 2 | 302 | 59.93% | 52.65% | +7.28 pp |
| 3 | 302 | 51.66% | 50.33% | +1.32 pp |

**Ninguna de las 60 configuraciones superó a la referencia en los tres bloques.** La mejora del candidato depende principalmente del segundo periodo. Además, escoger el mejor de 60 intentos introduce sesgo de selección: este promedio no es una estimación independiente de su rendimiento futuro.

El candidato usa XGBoost con 100 árboles, profundidad máxima 2, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8 y semilla 123; 50 rezagos más estadísticas móviles y hora UTC. Está guardado para investigación y **no se activó como modelo operativo**.

## Qué no podemos concluir

- No se ha demostrado rentabilidad: faltan precios ejecutables históricos suficientes, calibración y simulación con datos futuros.
- 54.64% de acierto no significa 54.64% de probabilidad fiable para cada compra.
- El stream verificado es una captura acotada, no un servicio continuo con rotación y recuperación de huecos.
- Las ganancias de pruebas unitarias sintéticas no son ganancias del bot.
- El reentrenamiento todavía se ejecuta mediante comandos; no hay un planificador automático ni promoción de modelos en funcionamiento.

## Revisión recomendada antes de la siguiente parte

El resultado justifica conservar la infraestructura y cambiar el foco hacia evidencia prospectiva: capturar continuamente con fechas reales de recepción, construir variables de microestructura disponibles a la apertura y medir candidatos en modo observación. Luego calibrar probabilidades, simular ejecuciones con costos reales y automatizar el reentrenamiento con criterios de promoción y reversión. Los 303 ejemplos finales deben seguir reservados hasta congelar el experimento que se decida evaluar.

El bot debe poder abstenerse. Que el software haga una compra sin intervención no demuestra que esa compra tenga ventaja económica.

## Archivos del repositorio

- `polymarket_public.py`, `collect_market.mjs`: lectura REST y captura WebSocket.
- `market_history.py`, `event_store.py`: historial auditado y persistencia.
- `training.py`, `optimize.py`: entrenamiento y búsqueda temporal reproducibles.
- `autonomy.py`: componentes Paper y controles temporales.
- `requirements-lock.txt`: versiones del entorno aislado `.venv`.
- `data/history_7d_20261117/`: respuestas originales, registros y auditoría.
- `models/research_notebook_20261117/`: modelos originales y métricas.
- `models/optimized_research_20261117/`: 60 experimentos y candidato.

Los directorios data, models y .venv están excluidos de Git. Los resultados aún no se han subido a un servicio externo. No se hicieron operaciones reales.

## Referencias técnicas

- [Stream público y requisitos del TWAP](https://docs.polymarket.com/market-data/realtime-data).
- [Comisiones por mercado](https://docs.polymarket.com/trading/fees).
- [Validación temporal de scikit-learn](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html).

## Verificación al cerrar la etapa

31 pruebas automatizadas aprobadas. git diff --check no reportó problemas. Se verificaron 26,163 eventos y 2,015 versiones de referencias en SQLite. El test final continúa reservado y la promoción del candidato está deshabilitada.

