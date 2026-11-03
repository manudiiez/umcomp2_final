# ADR-0005 — Rangos semiabiertos y propiedad de líneas

- Fecha: 2026-11-02.
- Estado: adoptado para implementación.
- Referencia: [PROJECT_STATEMENT.md](../PROJECT_STATEMENT.md).

## Contexto

Cortar por tamaño puede dividir registros. Descartar siempre la primera línea
cuando `inicio > 0` pierde una línea si el
corte ya coincide con su primer byte. Tampoco se puede suponer que toda línea
termina dentro de una única extensión de 64 KiB.

## Decisión

Calcular `[inicio, fin)` aritméticamente a partir del tamaño, sin leer contenido
en el coordinador. Una línea pertenece al tramo que contiene su primer byte.
El worker consulta el byte anterior a su inicio: si es LF ya está alineado; en
otro caso descarta la cola de la línea anterior. Procesa inicios estrictamente
menores que `fin` y completa el último registro hasta LF o EOF.

Leer extensiones en bloques repetibles de 64 KiB; un tramo puede no contener
ningún inicio de línea. Aceptar CRLF y última línea sin LF. Particionar antes de
decodificar UTF-8. No producir tareas ni mapas para un archivo vacío.
Los flags `--chunk-mb` y `--size-mb` usan MiB; 2 GiB con tramos de 32 MiB son 64 tareas.

## Alternativas

- Alinear en el coordinador: exige acceso al contenido y acopla planificación y lectura.
- Descartar primera línea incondicionalmente: incorrecto en un límite exacto.
- Generar archivos de fragmentos: agrega escritura/limpieza y duplica datos.
- Permitir registros partidos: altera conteos y produce falsos inválidos.

## Consecuencias

Puede haber lectura auxiliar solapada entre tareas, pero la **propiedad de los
registros** es disjunta. Una línea que atraviesa varios tramos solo se cuenta en
el primero que contiene su inicio. No afirmar que todos los bytes leídos son
disjuntos ni que cada tarea lee exactamente `fin-inicio` bytes.

## Verificación y referencias

CU-24; AC-03 y AC-04. Comparar contra referencia secuencial para cortes dentro,
sobre y después de LF, entre CR/LF, UTF-8 multibyte, EOF sin salto, líneas largas
y archivo vacío. El algoritmo normativo completo está en PROJECT_STATEMENT §8.
