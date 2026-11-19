> Diseño histórico del 17 de noviembre. La integración posterior del monitor, el reentrenamiento y la cartera simulada se documenta en el [README](../README.md#monitor-en-vivo-y-simulación). Los resultados de la investigación inicial figuran en [RESULTADOS_ETAPA_2.md](RESULTADOS_ETAPA_2.md).

# Autonomía, datos públicos y reentrenamiento

Decisiones del usuario, 17 de noviembre de 2026: objetivo económico de ganancias mediante predicciones; compras autónomas; empezar con simulación; límites de dinero real pendientes; datos en tiempo real de Polymarket y reentrenamiento automático; continuar con datos públicos sin acceso Chainlink TWAP por ahora. Se conservan modelos y parámetros del notebook, con predicción al inicio del intervalo. El protocolo DL y el +20% siguen retirados.

## Flujo objetivo

1. Descubrir mercados BTC 5 m y capturar eventos públicos de libro, operaciones y resolución. Guardar reglas y correspondencia de identificadores Up/Down.
2. Al inicio de cada intervalo, fijar una única predicción con datos recibidos a tiempo y versión de modelo. Las decisiones tardías se omiten.
3. Evaluar la compra: calidad de datos, modelo validado, probabilidad calibrada, precio ejecutable, profundidad, costos, saldo, exposición, presupuesto de pérdida y duplicados.
4. Comprar automáticamente en Paper cuando pase todos los filtros; abstenerse cuando no pase alguno. El LLM podrá aportar verificaciones cuando se integre su API, sin alterar los límites.
5. Conservar inicialmente la posición hasta resolución confirmada. Una salida anticipada será una estrategia distinta que requiere simulación propia. Registrar costos, cantidad, saldo, ganancia/pérdida y versión del modelo.
6. Incorporar el resultado confirmado al historial. Evaluar predicciones antiguas sin modificarlas retrospectivamente.
7. Reentrenar por lotes maduros y tiempo mínimo configurable. Evaluar el candidato sobre periodos posteriores al entrenamiento; mantener el modelo activo si el candidato no mejora. Conservar versiones y posibilidad de volver al modelo previo.

## Ganancias y probabilidad

Un acierto no asegura una buena compra. En el modelo simplificado de mantener hasta liquidación, una participación ganadora paga una unidad y una perdedora cero. Para cantidad q, probabilidad estimada p y costo total C, el beneficio esperado es p*q-C. El costo incluye compra y gastos adicionales; el precio de ejecución debe reflejar profundidad y deslizamiento.

Por ejemplo, estimar 60% de probabilidad y pagar 0.70 por participación tiene esperanza negativa incluso antes de costos. Una estimación de probabilidad equivocada puede producir pérdidas aunque el filtro indique ventaja positiva.

Los regresores del notebook no producen probabilidades calibradas por sí solos. Antes de utilizar este filtro se necesita un método de calibración y validación temporal sobre resultados Up/Down, con datos independientes del ajuste del regresor. La confianza verbal de un LLM no sustituye esa evidencia. Se mantienen las métricas del notebook y se incorpora evaluación de calibración y resultado neto Paper como parte de la estrategia solicitada.

Las comisiones se obtendrán de parámetros vigentes del mercado; no se fija una tarifa universal ni se presupone costo cero. La documentación consultada anuncia cambios para el 20 de noviembre de 2026, por lo que esa consulta deberá repetirse al implementar el adaptador de costos.

## Datos y aprendizaje automático continuo

Los libros Up/Down contienen precios de participaciones; no son una serie de cierres de BTC. El stream de mercado permite captar microestructura y eventos de resolución. El stream TWAP de Chainlink a través de Polymarket requiere autenticación y una clave del proveedor, todavía no disponible.

Se puede comenzar a recolectar datos públicos y resoluciones. La disponibilidad de un historial continuo de BTC compatible con las diferencias de precio del notebook sigue sin demostrarse. No se entrenará como si una serie de precios Up/Down concatenada entre mercados fuera una serie de BTC. Si el acceso público solo permite construir un modelo de microestructura/clasificación, se documentará y verificará esa adaptación antes de cambiar el objetivo del notebook.

Cada fila de entrenamiento tendrá mercado, inicio, fin, fecha de disponibilidad de entradas, resultado, fecha de disponibilidad de la etiqueta y evidencia de resolución. El reentrenamiento no utilizará mercados sin resolver ni predicciones propias como etiquetas. También debe verificar que la fuente y el resultado correspondan al mercado correcto, eliminar duplicados y detectar correcciones de resolución.

La división inicial permanece 80/20 cronológica. Selección y calibración se hacen dentro del 80% con bloques temporales. Tras evaluar el test final, las mejoras siguientes se miden hacia adelante con nuevas observaciones; no se reutiliza repetidamente ese mismo test como evidencia intacta. Cualquier intervalo entrenado se marca como tal. Retraining automático no implica modificación autónoma de límites, arquitectura o fuente sin registro.

## Implementado en esta etapa

- `autonomy.py`: evaluación determinista de compras Paper, ejecución sobre niveles ask, costos explícitos, límites de saldo/exposición/presupuesto diario, persistencia SQLite, una compra por mercado, liquidación binaria verificada e idempotencia.
- `training_eligible`: rechazo de filas con información posterior a la apertura, sin resolución final o con etiqueta que aún no estaba disponible al corte. Es un filtro temporal, no un entrenador.
- `collect_market.mjs`: captura cruda del WebSocket público para los identificadores de un mercado ya inspeccionado. Conserva hora de recepción, frames originales, conteos y cierre. Usa PING/PONG. No reconstruye todavía un libro validado para operar.
- Diez pruebas de los componentes Paper y elegibilidad con datos sintéticos. Los importes y probabilidades de esas pruebas son fixtures, no parámetros recomendados ni rendimiento observado.

La liquidación Paper trata los costos como importes equivalentes en efectivo y paga una unidad por participación ganadora. El adaptador de tarifas reales y sus reglas de redondeo todavía no existen. `model_approved`, `costs_verified` y `rules_verified` son contratos del llamador; debe respaldarlos con verificaciones reales. La simulación no modela aún cola de ejecución, cambios de profundidad durante la latencia, cancelaciones, salidas anticipadas ni resolución anulada. El cálculo diario usa UTC y limita pérdidas realizadas más el costo total de posiciones abiertas; no es un drawdown de patrimonio valorado a mercado.

## Ejecutar verificaciones

```powershell
python -m unittest discover -s tests -v
python polymarket_public.py --output data/current_snapshot.json
node collect_market.mjs data/current_snapshot.json data/stream_unique_name.jsonl 30
```

El capturador requiere Node.js 22 o posterior. Su archivo de salida debe ser nuevo. Cada prueba dura el tiempo indicado (máximo una hora) y se detiene; no queda un servicio desatendido. Para otro intervalo se requiere un snapshot nuevo. Captura eventos durante la prueba, sin emitir pronósticos retroactivos al inicio.

## Pendiente para el bot autónomo completo

Recepción continua con rotación de mercados, reconexión y recuperación de huecos; almacenamiento normalizado y verificación de resoluciones; contrato del dataset; entrenamiento y calibración; planificador de predicciones; parámetros Paper de estrategia; adaptador de costos; integración entre capturador/modelo/motor; métricas y supervisión; programador de reentrenamiento; promoción y reversión de versiones. No hay todavía un modelo activo ni autoentrenamiento en ejecución.

## Fuentes oficiales consultadas

- [Datos en tiempo real](https://docs.polymarket.com/market-data/realtime-data): stream de mercado, resolución, heartbeat y requisitos del TWAP.
- [Comisiones](https://docs.polymarket.com/trading/fees): parámetros por mercado y cambios anunciados.
- [Posiciones y liquidación](https://docs.polymarket.com/concepts/positions-tokens): pagos de resultados ganadores/perdedores.
- [Gestión de órdenes](https://docs.polymarket.com/trading/manage-orders): conciliación de órdenes y ejecuciones para una etapa posterior.
