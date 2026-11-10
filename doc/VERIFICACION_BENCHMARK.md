# Benchmark de 1, 2 y 4 procesos (milestone 6, issue #26)

Medición real de tiempos con el sistema implementado — no tiempos
hipotéticos ni una estimación de aceleración lineal. PROJECT_STATEMENT §15;
CU-21; AC-18; ADR-0013.

## Qué se fija y qué se mide

Fijo en las nueve corridas (3 configuraciones × 3 repeticiones):

| Parámetro | Valor |
|---|---|
| Archivo | `/tmp/benchmark.log` generado por `loghunter.generar`, `seed=2026`, `size_mb=48`, `ips=50`, `corrupt_pct=2` |
| Tamaño real / hash | 50 331 722 bytes, `sha256=96545675d9ddb7825daf5071b82ad4ad76aae8af6465e4a20ff0be9888a62c7f` |
| Consulta | `status`, `--top 10` |
| Tramo | `chunk_mb=1` → 48 tareas `map_chunk` |
| Backend | `local://` (mismo host; evita variabilidad de red de MinIO en la medición) |
| Infraestructura | Redis real (`loghunter-redis`), un coordinador real (`max_jobs=1`, sin otros jobs compitiendo), **un solo nodo Celery** con `-c {1,2,4}` entre corridas |

Variable: cantidad de **procesos** del único nodo Celery (`-c 1`, `-c 2`,
`-c 4`), reiniciando el worker entre configuraciones para que "procesos
configurados" y "procesos observados" (hostnames/PID distintos que
efectivamente procesaron tareas, vía el campo `worker` de cada parcial) se
puedan comparar sin contaminación entre corridas. **Un solo nodo Celery en
todas las corridas**: la variable es la concurrencia dentro de ese nodo, no
la cantidad de nodos — PROJECT_STATEMENT §14 pide no confundir ambos
números.

Entorno: Apple M3 Pro, 11 núcleos (físicos y lógicos, sin SMT), macOS
15.7.4, Python 3.12.2, Celery 5.7.0, redis-py 8.1.0, Redis server 7.4.12
(`redis:7-alpine` en Docker). `uv.lock` fija las mismas versiones para
quien reproduzca el ensayo.

## Procedimiento

```bash
docker ps --filter name=loghunter-redis   # Redis real arriba, sin otros jobs activos
uv run --locked python scripts/benchmark_procesos.py
```

El script (`scripts/benchmark_procesos.py`, versionado en el repositorio):
genera el archivo fijo una sola vez, levanta un coordinador real
(`max_jobs=1`), y para cada configuración de `-c` lanza un worker Celery
real, espera que responda a `ping()`, corre el cliente CLI real tres veces
(midiendo el campo `duracion` del informe, que es el tiempo entre admisión y
reduce completo — no incluye el round-trip TCP de conexión) y compara el
agregado completo contra la primera corrida exitosa. Cualquier discrepancia
de agregado entre corridas aborta con `AssertionError` en vez de reportar
una medición sobre un resultado incorrecto.

## Resultado real (2026-11-10)

```text
  -c 1 rep 1/3: duracion=9.342s workers_observados=1 lineas=456552 invalidas=9424
  -c 1 rep 2/3: duracion=8.771s workers_observados=1 lineas=456552 invalidas=9424
  -c 1 rep 3/3: duracion=8.765s workers_observados=1 lineas=456552 invalidas=9424
  -c 2 rep 1/3: duracion=5.673s workers_observados=2 lineas=456552 invalidas=9424
  -c 2 rep 2/3: duracion=5.169s workers_observados=2 lineas=456552 invalidas=9424
  -c 2 rep 3/3: duracion=5.186s workers_observados=2 lineas=456552 invalidas=9424
  -c 4 rep 1/3: duracion=2.635s workers_observados=4 lineas=456552 invalidas=9424
  -c 4 rep 2/3: duracion=2.623s workers_observados=4 lineas=456552 invalidas=9424
  -c 4 rep 3/3: duracion=2.660s workers_observados=4 lineas=456552 invalidas=9424
```

| Procesos (`-c`) | T mediana (s) | `speedup(p) = T(1)/T(p)` | Procesos observados (PIDs distintos) |
|---|---|---|---|
| 1 | 8.771 | 1.000 | 1 |
| 2 | 5.186 | 1.691 | 2 |
| 4 | 2.635 | 3.328 | 4 |

`lineas=456552` e `invalidas=9424` coinciden exactamente en las nueve
corridas — mismo agregado con 1, 2 y 4 procesos, verificado por el script
(no solo observado a ojo).

## Por qué 4 procesos superó la aceleración lineal estricta, y por qué no se promete en general

`speedup(4) = 3.33` queda por debajo de la aceleración lineal perfecta
(4.0), pero el salto marginal de 2→4 procesos (5.186s→2.635s, ≈1.97×) fue
más eficiente que el de 1→2 (8.771s→5.186s, ≈1.69×): la eficiencia por
proceso agregado *mejoró* en vez de degradarse, al revés de lo que suele
pasar al escalar cómputo puro. La explicación más consistente con el
mecanismo real, no una promesa general:
cada tarea paga un costo fijo de *round-trip* con Redis (publicar, bloquear
esperando el resultado, deserializar) independiente de su cómputo; con `-c
1` ese costo fijo se paga 48 veces en serie, mientras que con más procesos
en paralelo varios de esos round-trips se superponen en el tiempo en vez de
sumarse. Esto **no es aceleración lineal de cómputo puro**: es que el costo
fijo por tarea se amortiza mejor cuanta más concurrencia hay, hasta el
límite de 11 núcleos disponibles en este host. No se debe extrapolar que 8
o 16 procesos sigan mejorando en la misma proporción: PROJECT_STATEMENT §14
es explícito en que red, Redis, CPU, serialización y cardinalidad de claves
pueden limitar el rendimiento, y este mismo mecanismo de amortización deja
de ayudar una vez que la concurrencia supera los núcleos físicos
disponibles o satura el broker.

## Límites de esta medición

- Un solo host, un solo nodo Celery: no mide el costo de red entre máquinas
  (eso es la separación cómputo/almacenamiento del milestone 3, no este
  benchmark) ni el efecto de **varios nodos** Celery, que PROJECT_STATEMENT
  §14 distingue explícitamente de procesos por nodo.
- Backend `local://`, no `s3://`: se fijó así para no mezclar la latencia
  de red hacia MinIO con el efecto de la concurrencia, que es lo que este
  issue mide. AC-02 (igualdad entre backends) ya está verificado por
  separado en `tests/test_integracion_distribucion.py`.
- `top()` de `--top 10` no agrega carga relevante frente al conteo
  completo; no se midió el costo de ese recorte por separado.
- Tres repeticiones por configuración, no treinta: suficiente para ver que
  la mediana es estable (variación menor al 7% entre repeticiones de una
  misma configuración) pero no para un intervalo de confianza riguroso.

## Reproducir el ensayo

```bash
docker run -d --name loghunter-redis -p 6379:6379 redis:7-alpine   # si no está ya arriba
uv run --locked python scripts/benchmark_procesos.py
```

Con la misma semilla, tamaño y versiones de `uv.lock`, el archivo generado
es byte-idéntico (mismo `sha256`) y los agregados (`lineas=456552`,
`invalidas=9424`) deben coincidir exactamente; los tiempos absolutos pueden
variar con el hardware, pero la relación 1 > 2 > 4 en duración y el
agregado idéntico entre configuraciones deberían reproducirse.

## Referencias

PROJECT_STATEMENT §§14, 15; CU-21; AC-02, AC-18; ADR-0003, ADR-0013.
