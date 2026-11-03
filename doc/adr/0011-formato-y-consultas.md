# ADR-0011 — Parser combined y semántica de consultas

- Fecha: 2026-11-02.
- Estado: adoptado para implementación.
- Referencia: [PROJECT_STATEMENT.md](../PROJECT_STATEMENT.md).

## Contexto

Dos implementaciones podrían discrepar sobre zonas horarias, parámetros de URLs,
bytes ausentes o aplicación del top sin violar las descripciones informales.
El parser debe ser común a los workers y verificable de forma aislada.

## Decisión

Aceptar únicamente combined de Apache/Nginx, con validación completa mediante
`re` y validación de campos con biblioteca estándar. Aceptar IPv4/IPv6, escapes
de campos citados, meses ingleses, offset horario, LF/CRLF y último registro sin
LF. UTF-8 o campos inválidos suman una inválida; no abortan el tramo.

Definir `lineas` como válidas y `invalidas` por separado. Bytes `-` equivalen a
cero. `top-ips` cuenta IP normalizada; `urls` cuenta destino original con query
string; ambos aplican top global con desempate por clave ascendente.
`status` devuelve todos los códigos en orden numérico. `traffic` suma bytes por
fecha-hora UTC y devuelve todas las horas observadas en orden cronológico.

Mantener parser y agregadores separados de transporte/lectura. Los workers
devuelven todos sus contadores, aunque el cliente pida top 1.

## Alternativas

- Top por worker: pierde claves necesarias para obtener el ranking global exacto.
- Agrupar tráfico solo por hora 0–23: mezcla días; mantener hora local sin offset
  también puede confundir instantes.
- Quitar parámetros de URL: cambia la pregunta y fusiona destinos distintos.
- Convertir bytes ausentes en error de job: impide tratar registros combined válidos.
- Soportar formatos adicionales: excede el alcance actual.

## Consecuencias

Se obtienen resultados deterministas entre backends y cantidades de procesos.
La suma de `status`, IPs y URLs completos coincide con válidas; el
tráfico suma bytes, por lo que no se compara su suma con cantidad de líneas.

## Verificación y referencias

CU-02 a CU-05 y CU-12; AC-01. Usar el ejemplo normativo de PROJECT_STATEMENT §7.3,
datos con distintas zonas/días, escapes, bytes `-` e inválidas. Comprobar también
rankings con empates y resultados que requieren sumar claves de muchos tramos.
