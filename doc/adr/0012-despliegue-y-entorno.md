# ADR-0012 — Docker Compose, datos persistentes y workers remotos

- Fecha: 2026-11-02.
- Estado: adoptado para implementación.
- Referencia: [PROJECT_STATEMENT.md](../PROJECT_STATEMENT.md).

## Contexto

La instalación debe ser reproducible y mostrar que cómputo y almacenamiento no
necesitan compartir máquina. Replicar contenedores en un host demuestra capacidad
local, pero por sí solo no prueba distribución entre computadoras.

## Decisión

Desarrollar sobre CPython 3.12 y Linux como plataforma objetivo; permitir macOS
para desarrollo. Fijar versiones compatibles de dependencias e imágenes al
crear el entorno. Usar Compose con Redis, MinIO, coordinador y workers; el
indexador vive como proceso hijo en el contenedor del coordinador.

Usar volúmenes propios para MinIO y SQLite. En modo distribuido, ningún worker
necesita montar el archivo de log: lee por S3. Publicar/documentar puertos y
preparación de bucket/credenciales. Registrar persistencia o volatilidad de Redis
según la configuración elegida sin prometer HA.

Escalar localmente con réplicas y/o `-c`; para varios hosts, iniciar workers
externos con URLs alcanzables y credenciales del mismo entorno. Separar cantidad
de nodos Celery, procesos por nodo y procesos totales.

## Alternativas

- Solo instalación manual: posible como camino de desarrollo, menos reproducible
  para la presentación de todos los servicios.
- Compartir el log en un volumen local: no satisface por sí mismo la corrección
  de workers remotos.
- Kubernetes o almacenamiento replicado: complejidad fuera del alcance académico.
- Imágenes `latest`: dificultan reproducir instalación y mediciones.

## Consecuencias

Un volumen propio de persistencia no contradice evitar filesystem compartido del
log entre workers. `localhost` cambia de significado por contenedor/host. Cuatro
réplicas con `-c 4` equivalen a dieciséis procesos. La disponibilidad de las imágenes
fijadas debe verificarse al implementar; registrar cualquier cambio de tecnología.

## Verificación y referencias

CU-07 y CU-20; AC-08 y AC-17. Instalar desde checkout limpio, preservar datos al
recrear servicios y completar un job con un worker en otro host. Detalles del
contrato de despliegue en PROJECT_STATEMENT §§4, 12 y 14.
