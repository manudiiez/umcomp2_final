# ADR-0004 — Lector por rangos con backend local y S3

- Fecha: 2026-11-02.
- Estado: adoptado para implementación.
- Referencia: [PROJECT_STATEMENT.md](../PROJECT_STATEMENT.md).

## Contexto

El diseño inicial asumía rutas locales. Una cola distribuida no permite distribuir
el cómputo si sus ejecutores no pueden leer los datos desde otros hosts. Enviar
los gigabytes del archivo por Redis impondría carga innecesaria al broker.

## Decisión

Introducir `lector.py` con `abrir(uri)`, `tamano()`, `leer(inicio, fin)` y lectura
auxiliar hasta salto. Mantener dos implementaciones con intervalos semiabiertos:

- `local://`: `stat` y `mmap` de solo lectura para desarrollo/demo de un host.
- `s3://`: metadatos `HEAD` y `GET Range` con boto3 contra MinIO para varios hosts.

El coordinador solo consulta metadatos; los workers leen los datos directamente.
Para S3, convertir fin exclusivo a fin inclusivo HTTP. En local, alinear el mapa
a `ALLOCATIONGRANULARITY` y ajustar el offset interno. Liberar mapas, descriptores
y cuerpos de respuesta HTTP. El worker no contiene lógica específica del backend.

## Alternativas

- Solo rutas/volúmenes locales: requieren accesibilidad local y no resuelven por
  sí mismos el despliegue entre máquinas.
- NFS: sí permite acceso remoto por filesystem; se descarta por añadir gestión
  del filesystem de red, mientras S3 expresa directamente la lectura por rango.
- Solo S3: eliminaría el camino local con `mmap`, incluido para demostrar IPC.
- Mandar contenido por el broker: mezcla transporte de trabajo y de datos y aumenta
  memoria/tráfico de Redis.

## Consecuencias

Los workers remotos necesitan red hacia Redis y MinIO, endpoint y credenciales.
El modo local requiere el mismo host/ruta accesible; normalizar una URI no sube
archivos. S3 introduce tráfico de red y MinIO puede limitar rendimiento. `mmap`
aprovecha páginas de caché del kernel, pero convertir a `bytes` puede copiar datos.
La entrada debe permanecer inmutable durante el job.

## Verificación y referencias

Igualdad de resultados entre backends, rangos correctos y demostración en otro host:
CU-06, CU-07; AC-02 y AC-08. Referencias técnicas:
[GetObject de boto3](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/get_object.html)
y [mmap de Python](https://docs.python.org/3/library/mmap.html).
