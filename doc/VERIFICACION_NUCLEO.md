# Verificación del núcleo local (milestone 2)

Evidencia de avance parcial para el issue #8: el núcleo de análisis local
(parser, consultas, generador, lector y workers) funciona end-to-end sin
Redis, MinIO, Celery, sockets ni procesos adicionales. No es una demostración
cliente-servidor ni una entrega funcional completa; eso corresponde a
milestones posteriores.

## Qué queda demostrado

- Las cuatro consultas (`top-ips`, `status`, `urls`, `traffic`) calculadas
  por tramos (`workers.calcular_rangos` + `workers.map_chunk` +
  `consultas.reducir`) coinciden exactamente con la referencia secuencial
  (`consultas.calcular_referencia`) que procesa el archivo completo sin
  fragmentar.
- Los totales de líneas válidas e inválidas coinciden entre ambos caminos.
- La propiedad de las líneas se mantiene en casos límite: archivo vacío,
  archivo totalmente inválido, tramos de 1 byte, cortes dentro/sobre/después
  de un salto de línea, entre `\r` y `\n`, sobre caracteres UTF-8 multibyte,
  líneas mayores a 64 KiB y líneas que atraviesan varios tramos.

## Cómo reproducirlo

```bash
uv sync --locked --python 3.12
uv run --locked python -m pytest -q
```

Resultado real (2026-11-09, commit de este issue): **91 pruebas, 0 fallos**,
repartidas en `test_parser.py`, `test_consultas.py`, `test_generar.py`,
`test_lector.py`, `test_workers.py` y `test_integracion_nucleo.py` (este
último compara map-reduce contra la referencia secuencial con tramos de
64 KiB, 1 MiB y 700 003 bytes sobre un log generado de 2 MiB, además de los
casos límite de archivo vacío y archivo totalmente inválido).

## Verificación manual (sin pytest)

Generar un log reproducible y comparar manualmente el resultado por tramos
contra la referencia secuencial:

```bash
uv run --locked python -m loghunter.generar \
    -o /tmp/demo.log --size-mb 2 --ips 200 --corrupt-pct 5 --seed 20261109
```

```text
Generado 2097199 bytes en /tmp/demo.log (validas=18865, invalidas=959, total=19824)
```

```bash
uv run --locked python -c "
from pathlib import Path
from collections import Counter
from loghunter.consultas import calcular_referencia, presentar, reducir
from loghunter.workers import calcular_rangos, map_chunk

archivo = Path('/tmp/demo.log')
tamano = archivo.stat().st_size
rangos = calcular_rangos(tamano, 64 * 1024)
print(f'tamano={tamano} tareas={len(rangos)}')

for query in ('top-ips', 'status', 'urls', 'traffic'):
    parciales, validas, invalidas = [], 0, 0
    for inicio, fin in rangos:
        r = map_chunk(str(archivo), inicio, fin, query)
        parciales.append(Counter(r['contador']))
        validas += r['validas']
        invalidas += r['invalidas']
    total = reducir(parciales)
    ref, validas_ref, invalidas_ref = calcular_referencia(archivo, query)
    print(query, 'coincide=', presentar(query, total) == presentar(query, ref),
          'validas=', validas, 'invalidas=', invalidas)
"
```

Salida real obtenida:

```text
tamano=2097199 tareas=33
top-ips: coincide= True validas= 18865 invalidas= 959
status: coincide= True validas= 18865 invalidas= 959
urls: coincide= True validas= 18865 invalidas= 959
traffic: coincide= True validas= 18865 invalidas= 959
```

33 tareas de 64 KiB sobre un archivo de ~2 MiB (`ceil(2097199 / 65536) = 33`,
consistente con PROJECT_STATEMENT §8.1), con el mismo total de válidas e
inválidas que la referencia en las cuatro consultas.

## Límites de esta verificación

- Un solo proceso Python ejecuta tanto el cálculo por tramos como la
  referencia: todavía no hay Celery, Redis, sockets ni múltiples hosts.
  Esa verificación distribuida corresponde al milestone 3 (issue #14) y a
  la integración final (issue #25).
- El backend de lectura es solo `local://`; `s3://` se agrega en el issue #11.
- No se mide rendimiento ni paralelismo real; el benchmark con 1/2/4 procesos
  es el issue #26.

## Referencias

PROJECT_STATEMENT §§7, 8 y 12.3; CU-02 a CU-06, CU-11, CU-12 y CU-24;
AC-01 a AC-04 y AC-16; ADR-0001, ADR-0004, ADR-0005, ADR-0011 y ADR-0013.
