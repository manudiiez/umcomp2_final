# Diseño de CI para el flujo de LogHunter

Este documento describe validaciones a implementar cuando se solicite CI;
no afirma que ya haya workflows ni checks instalados.

| Base del PR | Origen permitido |
|---|---|
| milestone/N-slug | Rama de issue cuyo milestone real corresponde a esa base |
| develop | Rama milestone real, con issues y PRs de la entrega identificados |
| main | develop, con evidencia de integración y aceptación |

Validar formato Conventional Commits de commits y título del PR. Consultar el
issue y su milestone por datos reales de GitHub: el nombre de rama por sí solo
no demuestra la asociación. Las promociones incluyen sus issues/milestones.

Ejecutar las verificaciones apropiadas al cambio y los comandos que existan en el
repo. No instalar un check que siempre pase como si demostrara pruebas reales.
Documentar límites para PRs desde forks/permisos de lectura, sin ejecutar código
no confiable con credenciales elevadas para validar metadatos.

Hooks locales son ayuda y pueden omitirse; no sustituyen protecciones del servidor.
La configuración real se prepara conforme a [instalacion.md](instalacion.md).
No usar los workflows legacy de templates/: implementan testing/releases y rutas
incompatibles. No añadir builds, despliegues ni publicaciones de imágenes si no
forman parte de la tarea autorizada.
