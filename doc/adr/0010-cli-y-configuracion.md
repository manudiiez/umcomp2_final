# ADR-0010 — CLI y configuración explícita

- Fecha: 2026-11-02.
- Estado: adoptado para implementación.
- Referencia: [PROJECT_STATEMENT.md](../PROJECT_STATEMENT.md).

## Contexto

La materia pide argumentos por consola. El sistema debe poder ejecutarse en un
host o varios sin editar código, y su salida debe servir a personas y herramientas.
Las URLs y credenciales de servicios deben ser coherentes entre procesos.

## Decisión

Usar `argparse` en cliente, coordinador y generador, con los nombres de flags
definidos en PROJECT_STATEMENT §12. Usar la CLI propia de Celery para los workers. Todos los
ejecutables propios ofrecen ayuda y validación previa de argumentos.

Adoptar los defaults/rangos de PROJECT_STATEMENT §12. Respetar precedencia flags
explícitos → entorno → defaults. Configurar broker y backend por separado;
compartir endpoint S3 y credenciales por entorno. No incluir secretos en mensajes,
archivos versionados ni logs. La URI S3 identifica bucket/key, no las credenciales.

Ofrecer salida humana con progreso y tabla, y `--raw` con un único JSON terminal
en stdout. Enviar progreso/diagnóstico a stderr en ese modo. Códigos: 0 éxito,
2 argumentos inválidos y 1 fallo de servidor/conexión. Resolver IPv4/IPv6 según
la dirección; normalizar rutas locales sin transferir archivos.

## Alternativas

- Constantes editables en código: dificultan despliegue y no cumplen la CLI requerida.
- Solo interfaz web: fuera del alcance, agrega otra aplicación.
- Mezclar progreso y JSON en stdout: impide consumir el informe como documento JSON.
- Pasar secretos con cada consulta: innecesario y expone infraestructura al cliente.

## Consecuencias

Hay una interfaz estable para scripts y demos. Una ruta local del cliente solo
funciona si el procesamiento puede acceder a esa ruta; la normalización no es
subida. El modo raw no agrega una quinta consulta ni un protocolo diferente.

## Verificación y referencias

CU-01, CU-06, CU-10 y CU-15; AC-14. Verificar ayuda, precedencia, validaciones,
IPv4/IPv6, stdout JSON y códigos de salida. Contrato de CLI en PROJECT_STATEMENT §12.
