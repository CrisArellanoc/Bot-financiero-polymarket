# Verificación inicial

Fecha local: 17 de noviembre de 2026, 14:01:10, America/Mexico_City.
Captura UTC: 2026-11-17T20:01:10.098928+00:00.

## Evidencia real

- Gamma respondió con el evento `btc-updown-5m-1794945600`.
- Ventana verificada: 20:00–20:05 UTC (14:00–14:05 hora de Ciudad de México).
- Mercado versión v1; activo, no cerrado y aceptando órdenes en el momento de la captura.
- CLOB respondió para los dos identificadores de resultados; `connection_ok: true`; lista de errores vacía.
- Up: mejor compra ofertada 0.30, mejor venta ofertada 0.32; 30 niveles bid y 67 ask.
- Down: mejor compra ofertada 0.69, mejor venta ofertada 0.70; 69 niveles bid y 30 ask.
- Respuesta íntegra guardada en `data/connection_check.json`, excluida de Git. Las dos consultas de libro fueron secuenciales; no son una captura simultánea.

Las reglas recibidas vinculan la resolución a Chainlink BTC/USD TWAP y asignan Up en igualdad. La implementación exacta del cálculo y el historial disponible siguen pendientes de verificación antes de generar etiquetas.

## Pruebas locales

`python -m unittest discover -s tests -v`: 9 pruebas aprobadas, ejecutadas también desde la carpeta real del repositorio.

Cobertura: correspondencia etiqueta/identificador aunque cambie el orden; identificadores v2; rechazo de horizonte o esquema inválido; evento vacío; mejores precios sin depender del orden; libro vacío; NaN e identificador equivocado; tres intentos máximos ante HTTP 429; rechazo inmediato ante HTTP 403.

Las pruebas usan fixtures sintéticos para validar el conector; no se presentan como datos financieros. No son todavía las pruebas completas de entrenamiento, fuga temporal, simulador y agente exigidas por el protocolo.

## Alcance de esta evidencia

Se verificó lectura pública REST desde el equipo. No se verificó WebSocket, latencia continua, predicción a la apertura, entrenamiento, API privada del LLM ni rentabilidad. La lectura ocurrió dentro del intervalo y no se presenta como una predicción emitida al inicio.

La primera etapa dejó el conector, las pruebas, el diseño y la configuración experimental. Los pesos y escaladores aún no existen. No se enviaron órdenes, no se usaron credenciales y no se hizo commit ni push.
