# Verificación de recuperación y errores aislados (milestone 6, issue #23)

Evidencia de avance del issue #23: el **coordinador real** (asyncio, con
`loghunter.analisis` real, su indexador hijo real y, donde corresponde,
Celery workers reales) atraviesa fallos genuinos de infraestructura sin
perder ni duplicar registros, sin quedar bloqueado para otros pedidos y sin
que un job con fallo definitivo cancele a otro concurrente.

PROJECT_STATEMENT §§9.2, 9.3, 11, 13; CU-13 a CU-16, CU-22, CU-23;
AC-09, AC-10, AC-11, AC-19, AC-20.

## Qué ya estaba cubierto y no se repite

`tests/test_coordinador.py`, `tests/test_coordinador_persistencia.py` y
`tests/test_operacion.py` (milestones 4 y 5) ya verifican con dobles
(`lanzar_grupo`/`reducir_resultados` falsos) la lógica de timeout, fallo de
tarea, `SERVICE_UNAVAILABLE` por indexador caído, `BROKER_UNAVAILABLE` al
publicar y desconexión de cliente — incluyendo una caída **real** del
indexador (proceso hijo real con `multiprocessing.Process`, señales reales
por `os.kill`). `tests/test_integracion_distribucion.py` (milestone 3) ya
verificó la pérdida de un worker real a través de `analisis.lanzar_grupo`
directo. Ese trabajo no se duplica acá.

## Qué agrega este issue

`tests/test_fallos.py` agrega lo que los dobles no pueden demostrar: el
**coordinador/cliente real completo** atravesando fallos de infraestructura
genuinos (no simulados), verificados automáticamente con `pytest` (se saltan
solos si no hay Redis/MinIO reales en `localhost`, igual que
`test_integracion_distribucion.py`):

| Prueba | Qué fuerza de verdad | Qué confirma |
|---|---|---|
| `test_fallo_definitivo_de_tarea_no_afecta_a_un_job_concurrente_sano` | Se revoca el permiso de lectura del archivo de un job justo después de calcular sus tramos (antes de publicarlos): el worker real falla de verdad al leer su tramo | `TASK_FAILED` finito; un segundo job concurrente y sano, en el mismo coordinador, se completa sin verse afectado (AC-10) |
| `test_minio_caido_produce_error_finito_y_el_coordinador_sigue_atendiendo` | Se detiene el contenedor `loghunter-minio` con `docker stop` antes de pedir el tamaño de un objeto `s3://` | `STORAGE_UNAVAILABLE` finito, sin credenciales filtradas en el mensaje; el coordinador sigue vivo y atiende un pedido siguiente tras restaurar MinIO (AC-10, AC-20) |
| `test_redis_caido_al_publicar_produce_broker_unavailable` | Se detiene el contenedor `loghunter-redis` con `docker stop` antes de publicar las tareas de un job | `BROKER_UNAVAILABLE` finito en vez de quedar esperando indefinidamente (AC-10) |
| `test_timeout_real_no_cancela_un_job_concurrente_y_libera_cupo` | Se congela (`SIGSTOP`, no `SIGKILL`) el único worker Celery real a mitad de un job, simulando un ejecutor que dejó de responder sin haber caído | `JOB_TIMEOUT` al vencer el deadline real del job; un segundo job concurrente sin tareas que publicar se completa igual, sin cancelarse (AC-11) |

Cada prueba restaura el contenedor/proceso que detuvo en su propio `finally`,
con espera activa a que vuelva a responder antes de continuar.

Resultado real (2026-11-10):

```bash
AWS_ACCESS_KEY_ID=minioadmin AWS_SECRET_ACCESS_KEY=minioadmin AWS_DEFAULT_REGION=us-east-1 \
uv run --locked python -m pytest -q tests/test_fallos.py
# 4 passed in 35.38s
```

con Redis y MinIO reales (`loghunter-redis`/`loghunter-minio`) arriba; se
saltan con motivo explícito si no lo están. El resto de la suite
(`uv run --locked python -m pytest`) sigue en 204 passed / 12 skipped tras
agregar este archivo, sin regresiones.

## Pérdida de un worker a mitad de job, a través del coordinador real (AC-09)

**No** se automatizó como prueba de `pytest`: la recuperación real del
transporte Redis de Celery (`visibility_timeout` + reentrega de mensajes no
confirmados) tarda muchos segundos más que el valor configurado — ya se
había observado en el milestone 3 (`doc/VERIFICACION_DISTRIBUCION.md`: ~200 s
con `visibility_timeout=10`); acá, con solo 5 tramos y
`visibility_timeout=8`, tardó **98 s**. Automatizarlo haría la suite
excesivamente lenta sin agregar certeza sobre un mecanismo ya verificado; es
la misma razón por la que esa prueba tampoco está en
`tests/test_integracion_distribucion.py`. Se verificó manualmente, pero esta
vez a través del **coordinador asyncio y el protocolo TCP reales** (milestone
4/5), no invocando `analisis.lanzar_grupo` directo como hizo el milestone 3.

Procedimiento (infraestructura según INSTALL.md: `loghunter-redis` y
`loghunter-minio` arriba):

```bash
AWS_ACCESS_KEY_ID=minioadmin AWS_SECRET_ACCESS_KEY=minioadmin AWS_DEFAULT_REGION=us-east-1 \
uv run --locked python - << 'PY'
import asyncio, json, os, subprocess, sys, time
from pathlib import Path

os.environ.setdefault("LOGHUNTER_VISIBILITY_TIMEOUT", "8")

def worker(nombre):
    env = dict(os.environ)
    return subprocess.Popen(
        [sys.executable, "-m", "celery", "-A", "loghunter.tasks", "worker",
         "--loglevel=WARNING", "-c", "1", "-n", f"{nombre}@%h",
         "--without-gossip", "--without-mingle", "--without-heartbeat"],
        env=env,
    )

async def principal():
    from loghunter import coordinador, protocolo
    from loghunter.consultas import calcular_referencia
    from loghunter.generar import escribir_log

    ruta = "/tmp/demo_recuperacion.log"
    escribir_log(destino=ruta, size_mb=4, ips=20, corrupt_pct=1, seed=99)
    referencia, validas, invalidas = calcular_referencia(Path(ruta), "status")

    w1, w2 = worker("superviviente"), worker("victima")
    from loghunter.tasks import app
    for _ in range(40):
        act = app.control.inspect(timeout=1.0).ping()
        if act and len(act) >= 2:
            break
        await asyncio.sleep(0.5)

    coord = coordinador.Coordinador(max_jobs=1, job_timeout=150, chunk_mb_default=1)
    servidor = await asyncio.start_server(coord.atender_cliente, host="127.0.0.1", port=0, limit=protocolo.LIMITE_PEDIDO)
    puerto = servidor.sockets[0].getsockname()[1]
    coord.iniciar_servicios(servidor, "/tmp/demo_recuperacion.sqlite")

    t0 = time.monotonic()
    reader, writer = await asyncio.open_connection("127.0.0.1", puerto)
    writer.write(protocolo.codificar({"version": protocolo.VERSION, "type": "analyze",
                  "file": f"local://{ruta}", "query": "status", "top": 10, "chunk_mb": 1}))
    await writer.drain()

    async def leer():
        return json.loads(await reader.readline())

    await leer(); iniciado = await leer()
    print(f"t={time.monotonic()-t0:.2f}s chunks={iniciado['chunks']}")
    await asyncio.sleep(0.3)
    print(f"t={time.monotonic()-t0:.2f}s matando victima pid={w2.pid}")
    w2.kill()

    while True:
        m = await leer()
        if m["type"] == "progress":
            print(f"t={time.monotonic()-t0:.2f}s progreso={m['completed']}/{m['total']}")
            continue
        break
    print(f"t={time.monotonic()-t0:.2f}s terminal={m['type']}")
    if m["type"] == "result":
        agregado = {i["clave"]: i["valor"] for i in m["resultados"]}
        print(f"lineas={m['lineas']} invalidas={m['invalidas']} workers={m['workers']}")
        print("COINCIDE:", agregado == dict(referencia.most_common(10))
              and m["lineas"] == validas and m["invalidas"] == invalidas)

    writer.close(); await coord._apagar(); servidor.close(); await servidor.wait_closed()
    w1.kill(); w2.kill()

asyncio.run(principal())
PY
```

Salida real:

```text
workers listos: ['victima@MacBook-Pro-de-Manuel.local', 'superviviente@MacBook-Pro-de-Manuel.local']
t=0.00s accepted=admitted chunks=5
t=0.30s matando victima pid=35126
t=0.30s progreso=0/5
t=1.03s progreso=4/5
t=98.02s terminal type=result
lineas=38257 invalidas=378 workers_observados=2
referencia: validas=38257 invalidas=378
COINCIDE: True
```

El tramo que tenía en vuelo el worker `victima` al morir se redistribuyó al
`superviviente` después del `visibility_timeout`; el agregado final,
obtenido a través del protocolo TCP del coordinador real (no de `analisis`
directo), coincide exactamente con la referencia secuencial: ni se perdió ni
se contó dos veces ningún tramo.

## Límites de esta verificación

- Mismo límite de infraestructura que el milestone 3: un solo host físico;
  no hay una segunda máquina real para separar el worker "remoto".
- `LOGHUNTER_VISIBILITY_TIMEOUT` se redujo a 8s solo para la demostración
  manual de pérdida de worker; el default de producción (180s) no cambia.
- No se mide rendimiento ni se compara 1/2/4 procesos; eso es el benchmark
  del issue #26.
- Los escenarios de MinIO/Redis caídos detienen contenedores reales del
  entorno de desarrollo del operador (`loghunter-redis`/`loghunter-minio`);
  cada prueba los restaura antes de terminar, pero no debe ejecutarse esta
  suite si esos contenedores tienen datos de desarrollo que no toleran una
  breve interrupción.
- Un "ejecutor congelado" (`SIGSTOP`) no es idéntico a un host caído, pero
  es una señal más fiel que "ningún worker arriba" para demostrar que el
  coordinador revoca tareas realmente pendientes en un ejecutor real al
  vencer el deadline, sin depender de que el ejecutor haya muerto.

## Referencias

PROJECT_STATEMENT §§9.2, 9.3, 11, 13; CU-13 a CU-16, CU-22, CU-23;
AC-09, AC-10, AC-11, AC-19, AC-20; ADR-0003, ADR-0007, ADR-0008, ADR-0009.
