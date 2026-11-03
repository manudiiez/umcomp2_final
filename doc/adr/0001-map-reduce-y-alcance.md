# ADR-0001 — Map-reduce sobre logs y alcance acotado

- Fecha: 2026-11-02.
- Estado: adoptado para implementación.
- Referencia: [PROJECT_STATEMENT.md](../PROJECT_STATEMENT.md).

## Contexto

Las consultas elegidas suman ocurrencias o bytes de registros independientes.
Recorrer gigabytes con un solo proceso desperdicia la posibilidad de repartir
el cálculo. La materia requiere sockets concurrentes, IPC, asincronismo y una
cola distribuida; se debe poder explicar el rol real de cada mecanismo.

## Decisión

Implementar LogHunter en Python como cliente-servidor con map-reduce: tareas
independientes producen contadores completos y el coordinador suma sus claves.
Dividir el trabajo por rangos de bytes sin crear archivos fragmentados.
Ofrecer `top-ips`, `status`, `urls` y `traffic` sobre archivos combined inmutables.
Conservar en el histórico agregados y métricas, sin importar los logs a una base.

## Alternativas

- Script secuencial: útil como referencia de corrección, insuficiente como sistema
  distribuido y concurrente del trabajo final.
- Indexar todos los registros antes de consultar: requiere otro modelo de datos,
  ingestión y almacenamiento fuera del alcance acordado.
- Motor de streaming o plataforma de búsqueda completa: agrega servicios y
  funcionalidad ajenos al objetivo académico.

## Consecuencias

Se pueden sumar procesos y hosts porque el map de un rango no depende de otro.
Las nuevas consultas deben tener agregados combinables; sesiones o relaciones
entre registros pueden necesitar una arquitectura distinta. Cada pedido relee
el archivo. No se incluyen seguimiento en vivo, compresión, otros formatos,
interfaz web, autenticación de clientes ni alta disponibilidad.

El top se calcula después de agregar todos los parciales. La cardinalidad de
claves afecta memoria y tráfico de resultados; no se promete memoria constante.

## Verificación y referencias

Comparar las cuatro consultas con una referencia secuencial y verificar igualdad
al cambiar tramos/procesos. Referencias: PROJECT_STATEMENT §§2–3, 7 y 15;
CU-01 a CU-05 y CU-21; AC-01 y AC-18.
