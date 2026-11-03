# ADR-0006 — Protocolo propio sobre TCP con JSON por línea

- Fecha: 2026-11-02.
- Estado: adoptado para implementación.
- Referencia: [PROJECT_STATEMENT.md](../PROJECT_STATEMENT.md).

## Contexto

El cliente necesita enviar una consulta pequeña y recibir progreso e informe.
TCP proporciona un flujo de bytes, por lo que la aplicación debe delimitar
mensajes. No existe equivalencia garantizada entre una escritura y una lectura.

## Decisión

Usar TCP, JSON UTF-8 y LF como delimitador. Una conexión contiene un pedido y
respuestas `accepted`, `started`, `progress`, seguidas por `result` o `error`.
Versionar el contrato con `version: 1` y correlacionar respuestas con `job_id`.
Los esquemas y códigos están en PROJECT_STATEMENT §10.

Validar en cliente y servidor; limitar pedidos a 64 KiB y su lectura inicial a
30 s. Los informes pueden ser mayores. Ante campos/versiones desconocidos,
tipos incorrectos o framing inválido, responder con error si el canal lo permite
y cerrar. El terminal es único; un cierre sin terminal no equivale a éxito.
JSON también es la serialización aceptada de tareas/resultados de Celery.

## Alternativas

- UDP: requiere resolver pérdidas, orden y retransmisión en la aplicación.
- `pickle` recibido de clientes: puede ejecutar código al deserializar y acopla
  el protocolo a Python.
- Prefijo binario de longitud: válido, pero LF basta para estos objetos JSON.
- HTTP/REST: viable, pero los sockets y su protocolo forman parte del trabajo.

## Consecuencias

El protocolo es inspeccionable y no obliga a clientes Python. Hay que manejar
fragmentación, tamaños, desconexiones y backpressure. Los saltos dentro de cadenas
deben estar escapados por JSON. La versión inicial no ofrece reconexión ni reanudar
jobs; tampoco incorpora autenticación de clientes.

## Verificación y referencias

CU-01, CU-10 y CU-15; AC-14 y AC-15. Incluir fragmentación en varios envíos,
mensajes recibidos juntos y entradas inválidas, sin ejecutar datos del cliente.
Contrato completo: PROJECT_STATEMENT §10.
