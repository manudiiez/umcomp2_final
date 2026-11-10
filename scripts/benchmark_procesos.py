"""Benchmark real de 1, 2 y 4 procesos (issue #26). No se ejecuta como parte
de la suite de pytest: es una medición puntual con infraestructura real,
documentada con su salida en doc/VERIFICACION_BENCHMARK.md. Requiere Redis
real alcanzable en CELERY_BROKER_URL/CELERY_RESULT_BACKEND (ver INSTALL.md)
y backend local://, sin otros jobs compitiendo por el mismo coordinador."""
import hashlib, json, os, statistics, subprocess, sys, time
from pathlib import Path

os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:6379/0")
os.environ.setdefault("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

RUTA_LOG = "/tmp/benchmark.log"
SEED = 2026
SIZE_MB = 48
CHUNK_MB = 1
QUERY = "status"
TOP = 10
REPETICIONES = 3
CONFIGURACIONES = [1, 2, 4]
PUERTO = 8899
DB_PATH = "/tmp/benchmark_informes.sqlite"


def preparar_log():
    from loghunter.generar import escribir_log
    escribir_log(destino=RUTA_LOG, size_mb=SIZE_MB, ips=50, corrupt_pct=2, seed=SEED)
    sha = hashlib.sha256(Path(RUTA_LOG).read_bytes()).hexdigest()
    tamano = Path(RUTA_LOG).stat().st_size
    return sha, tamano


def lanzar_coordinador():
    env = dict(os.environ)
    return subprocess.Popen(
        [sys.executable, "-m", "loghunter.coordinador",
         "-i", "127.0.0.1", "-p", str(PUERTO),
         "--chunk-mb", str(CHUNK_MB), "--max-jobs", "1", "--job-timeout", "600",
         "--db", DB_PATH],
        env=env, cwd=str(RAIZ),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def lanzar_worker(procesos):
    env = dict(os.environ)
    return subprocess.Popen(
        [sys.executable, "-m", "celery", "-A", "loghunter.tasks", "worker",
         "--loglevel=WARNING", "-c", str(procesos), "-n", f"bench{procesos}@%h",
         "--without-gossip", "--without-mingle", "--without-heartbeat"],
        env=env, cwd=str(RAIZ), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def esperar_worker(nombre_prefijo, plazo=30):
    from loghunter.tasks import app
    t0 = time.monotonic()
    while time.monotonic() - t0 < plazo:
        try:
            act = app.control.inspect(timeout=1.0).ping()
        except Exception:
            act = None
        if act:
            return True
        time.sleep(0.5)
    return False


def correr_cliente():
    resultado = subprocess.run(
        [sys.executable, "-m", "loghunter.cliente", "-i", "127.0.0.1", "-p", str(PUERTO),
         "-f", f"local://{RUTA_LOG}", "-q", QUERY, "--top", str(TOP),
         "--chunk-mb", str(CHUNK_MB), "--raw"],
        capture_output=True, text=True, cwd=str(RAIZ), timeout=600,
    )
    if resultado.returncode != 0:
        raise RuntimeError(f"cliente falló: {resultado.stderr}")
    return json.loads(resultado.stdout)


def main():
    sha, tamano = preparar_log()
    print(f"archivo={RUTA_LOG} tamano={tamano} sha256={sha}")
    print(f"seed={SEED} size_mb={SIZE_MB} query={QUERY} top={TOP} chunk_mb={CHUNK_MB}")

    coord = lanzar_coordinador()
    time.sleep(1.5)

    referencia = None
    filas = []
    try:
        for procesos in CONFIGURACIONES:
            worker = lanzar_worker(procesos)
            try:
                if not esperar_worker(f"bench{procesos}"):
                    raise RuntimeError(f"worker -c {procesos} no respondió a tiempo")

                duraciones = []
                pids_observados = set()
                for rep in range(REPETICIONES):
                    informe = correr_cliente()
                    duraciones.append(informe["duracion"])
                    agregado = {item["clave"]: item["valor"] for item in informe["resultados"]}
                    if referencia is None:
                        referencia = (informe["lineas"], informe["invalidas"], agregado)
                    else:
                        assert (informe["lineas"], informe["invalidas"], agregado) == referencia, (
                            f"agregado distinto en -c {procesos} rep {rep}"
                        )
                    print(f"  -c {procesos} rep {rep+1}/{REPETICIONES}: "
                          f"duracion={informe['duracion']:.3f}s workers_observados={informe['workers']} "
                          f"lineas={informe['lineas']} invalidas={informe['invalidas']}")
                mediana = statistics.median(duraciones)
                filas.append({"procesos": procesos, "duraciones": duraciones, "mediana": mediana})
            finally:
                worker.terminate()
                try:
                    worker.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    worker.kill()
                    worker.wait(timeout=5)
                time.sleep(1)
    finally:
        coord.terminate()
        try:
            coord.wait(timeout=10)
        except subprocess.TimeoutExpired:
            coord.kill()

    t1 = next(f["mediana"] for f in filas if f["procesos"] == 1)
    print()
    print(f"{'procesos':>8} {'mediana(s)':>12} {'speedup':>10}")
    for f in filas:
        print(f"{f['procesos']:>8} {f['mediana']:>12.3f} {t1 / f['mediana']:>10.3f}")

    print()
    print("JSON:", json.dumps({"sha256": sha, "tamano": tamano, "filas": filas}))


if __name__ == "__main__":
    main()
