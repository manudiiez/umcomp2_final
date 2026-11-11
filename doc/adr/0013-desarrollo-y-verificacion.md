# ADR-0013 — Desarrollo incremental y verificación reproducible

- Fecha: 2026-11-02.
- Estado: adoptado para implementación.
- Referencia: [PROJECT_STATEMENT.md](../PROJECT_STATEMENT.md).

## Contexto

La evaluación requiere progresión visible en git y justificación de mecanismos.
Mostrar una tabla de tiempos sin controlar datos, procesos y resultados no
demuestra corrección ni aceleración. Se requieren mediciones del sistema implementado.

## Decisión

Trabajar incrementalmente en ramas de issue asociadas a milestones, con Conventional
Commits frecuentes y descripciones en castellano, siguiendo ADR-0014. Integrar por
PR de issue a milestone, de milestone a `develop` para pruebas y de `develop` a
`main`, sin commits de trabajo ni push directo a ramas de integración.
Mantener especificación, casos y ADR junto al código. Implementar
pruebas con `pytest`, referencia secuencial y ensayos de integración reales con
Redis/MinIO; no reemplazar toda la evidencia distribuida con mocks.

Crear `generar.py` con tamaño objetivo, cardinalidad de IPs, porcentaje de
corrupción y semilla. Usar PRNG/fecha base reproducibles, generar por bloques y
producir los mismos bytes para los mismos parámetros. Completar la última línea
al alcanzar tamaño; informar tamaño exacto y cantidades realmente generadas.

Medir 1, 2 y 4 procesos con archivo/hash, consulta, tramos, backend y entorno
constantes, al menos tres repeticiones y mediana. Registrar procesos configurados,
participantes observados y condiciones de caché; calcular `T(1)/T(p)`. Verificar
igualdad de agregados en cada corrida, separando benchmarks de demos concurrentes.

## Alternativas

- Un único commit final: incumple la progresión de desarrollo pedida.
- Datos aleatorios sin semilla/fecha fija: cambian la carga entre corridas.
- Solo observar que aparecen cuatro procesos: no prueba que el resultado sea
  correcto ni que se utilicen simultáneamente.
- Usar tiempos hipotéticos o asumir speedup lineal: no es evidencia experimental.

## Consecuencias

Las pruebas deben concentrarse en riesgos reales: límites de líneas, top global,
correlación, errores, cancelación, transacciones y recuperación. Los resultados
de rendimiento pueden no mejorar linealmente; informar límites de red/CPU/almacenamiento.
README, INSTALL, INFO y TODO forman parte de la entrega, junto a la evidencia.

## Verificación y referencias

CU-11 y CU-21; AC-16 y AC-18, más matriz completa de PROJECT_STATEMENT §15.
Requisitos académicos y entregables reproducidos en PROJECT_STATEMENT §16;
[consigna de la cátedra](../../../UM_C2/FINAL.md).
