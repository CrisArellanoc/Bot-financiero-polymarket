> Estado actualizado: ver RESULTADOS_ETAPA_2.md. Ya existen modelos de investigación entrenados; ningún modelo operativo está aprobado. Este documento conserva el diseño y distingue los componentes aún pendientes.

# Diseño vigente: parámetros del notebook

Actualizado el 17 de noviembre de 2026 por instrucción del usuario, quien confirma autorización del profesor. Este documento reemplaza el diseño inicial basado en el protocolo DL.

## Alcance

Predecir Up/Down de BTC en Polymarket, una vez al inicio de cada intervalo de cinco minutos. Se retiran la meta de +20%, los requisitos del protocolo, la partición 70/15/15, los 20 rezagos, el mínimo fijo de 1,500 datos, las redes MLP/LSTM obligatorias y la exigencia de Colab T4. La integración posterior con IA local permanece pendiente de la API del usuario.

El notebook es la referencia vigente. Contiene 64 celdas Markdown con ejemplos de código, no un entrenamiento ejecutado. Sus resultados de EUR/USD no acreditan capacidad predictiva para BTC ni constituyen metas de exactitud.

## Parámetros reproducidos

| Modelo | Rezagos | Parámetros explícitos | Transformación de entradas |
|---|---:|---|---|
| DummyRegressor | 500 | strategy=mean | Ninguna |
| LinearRegression | 500 | Valores predeterminados | Ninguna |
| SVR | 50 | kernel=rbf, C=1, gamma=0.04, epsilon=0.01 | StandardScaler |
| SGDRegressor | 500 | max_iter=50, tol=0.001 | StandardScaler |
| KNeighborsRegressor | 500 | n_neighbors=10 | Ninguna |
| DecisionTreeRegressor | 500 | random_state=123 | Ninguna |
| RandomForestRegressor | 500 | max_depth=20, random_state=123 | Ninguna |
| AdaBoostRegressor | 500 | random_state=123 | Ninguna |
| XGBRegressor | 500 | random_state=123, n_estimators=16, max_depth=12 | Ninguna |

La regla general es 500 rezagos y la excepción explícita es SVR con 50. Al convertir los ejemplos en módulos independientes, se configurará cada modelo explícitamente para evitar que el cambio de `num_lags` de SVR afecte accidentalmente a los siguientes. Los demás parámetros seguirán los valores predeterminados de las versiones que se instalen y registren; no se presupone que coincidan con una versión histórica no identificada. SGD no fija semilla en el ejemplo; las repeticiones deberán documentar cualquier semilla añadida para reproducibilidad.

División cronológica: primer 80% de ejemplos para entrenamiento y último 20% para prueba, después de construir ventanas. No hay un 15% adicional reservado a validación. Los escaladores se ajustan únicamente con entrenamiento.

## Transformación y objetivo

El notebook usa `np.diff(close)`: diferencias absolutas de precio, aunque las llama rendimientos. No equivale a `(P[t+1]/P[t])-1`. La configuración vigente conserva diferencias de precio y sus rezagos; el dataset documentará unidades y fuente. Un parámetro como `epsilon=0.01` depende de esas unidades: su valor inicial se copia del notebook, sin afirmar que sea adecuado para BTC.

En barras de cinco minutos, 500 rezagos corresponden a 41 horas y 40 minutos de historia; 50 a 4 horas y 10 minutos. No son horizontes de predicción: el horizonte sigue siendo cinco minutos. El tamaño final del historial se decidirá según cobertura, disponibilidad y estabilidad; no basta contar 501 cierres para evaluar un modelo con 500 entradas.

Los modelos del notebook son regresores. Producirán una diferencia estimada, cuya dirección se evaluará frente al resultado del mercado. Esa salida no es una probabilidad calibrada de acierto. El objetivo continuo y su correspondencia con Up/Down deben definirse sobre la referencia y reglas de resolución verificadas. No se sustituirá automáticamente por el cierre spot de otro exchange.

La captura pública del 17 de noviembre almacenó reglas que utilizan Chainlink BTC/USD TWAP y asignan Up en igualdad. Se conservarán reglas, resolución oficial y disponibilidad temporal por evento. El feed histórico y el cálculo exacto de la referencia aún requieren verificación. A la apertura solo se permitirán datos ya recibidos; las observaciones tardías se registrarán sin retrofechar predicciones.

## Evaluación

Las métricas principales del notebook, sección 6, son exactitud direccional, RMSE, sesgo alcistas/bajistas y correlación entre predicción y realidad. MAE y MSE también se describen y pueden acompañar el informe. Las métricas financieras descritas son rendimiento neto, factor de beneficio, ganancias/pérdidas medias, máximo drawdown y Sharpe; requieren un simulador y no se deducen del porcentaje de aciertos.

Se reportarán por separado la coincidencia de signos del regresor y el acierto Up/Down del contrato. Debe definirse el tratamiento de ceros; en el mercado inspeccionado un empate corresponde a Up. Correlaciones con predicciones constantes y cocientes con denominador cero serán no definidos, con explicación.

Para seleccionar entre los nueve modelos sin consumir el 20% final, se propone validación cronológica dentro del 80% de entrenamiento. Es un control de evaluación del proyecto, no una partición exigida por el notebook. Se fijarán fechas comunes para comparar modelos con 500 y 50 rezagos; no se compararán errores calculados en periodos distintos. Los parámetros copiados serán el experimento inicial; cualquier ajuste posterior quedará identificado y se decidirá sin usar el test final.

La meta de éxito es ganancia neta después de costos y controles de riesgo, respaldada por desempeño predictivo fuera de muestra. Ver AUTONOMIA.md para el alcance actualizado. El umbral numérico de aceptación sigue abierto: no se inventa un objetivo de acierto ni se promete fiabilidad antes de observar resultados. Se informará el tamaño de muestra, el comportamiento por periodo y la incertidumbre.

## Estado y siguiente etapa

El conector Gamma/CLOB y sus nueve pruebas siguen disponibles. El cambio actual modifica especificación y configuración; todavía no se entrenaron modelos, no existen pesos ni escaladores y no se ha construido el dataset histórico.

La siguiente etapa consiste en verificar datos históricos y etiquetas oficiales, establecer el contrato temporal y construir ejemplos. Después se implementará el entrenamiento de referencias y modelos clásicos con estos parámetros. El LLM local será una integración separada; no sustituirá la medición predictiva.

## Trazabilidad

- Fuente: `Aprendizaje_automatico_series_financieras_cap7 (1).ipynb`.
- SHA256: `76A39A7EC13394F6FFB8A57BD8AB606E544DB3E7E5FAE0E1B7C1F9DB646DE03C`.
- Celdas: 5–7 transformación, ventana, división y Dummy; 22 métricas principales; 29 lineal; 44 SVR; 49 SGD; 53 KNN; 57–60 árboles y ensembles. Numeración desde cero.
- Configuración legible por máquina: `config/experiments.json`.
- La verificación de conexión anterior se conserva en `docs/VERIFICACION.md`; describe lo observado en esa etapa, no los requisitos vigentes.


La ampliación vigente de autonomía, simulación y reentrenamiento está en `docs/AUTONOMIA.md`.
