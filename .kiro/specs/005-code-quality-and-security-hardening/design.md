# Design - Code Quality and Security Hardening

## Enfoque

La mejora de calidad se hará de forma incremental. Primero se audita, luego se prioriza y finalmente se aplican cambios pequeños.

## Áreas de revisión

- FastAPI routes.
- Application services.
- Domain logic.
- Infrastructure vision pipeline.
- Local persistence.
- Configuration.
- Benchmark scripts.

## Seguridad

Se usará el modelo CIA:

- Confidentiality: no exponer secretos ni rutas sensibles.
- Integrity: validar entradas, modelos, archivos y resultados.
- Availability: evitar bloqueos, sobrecalentamiento y fallos no controlados.

## Restricciones

- No ejecutar inferencias pesadas automáticamente.
- No agregar dependencias sin justificación.
- No aplicar refactor masivo.
- No cambiar modelos sin benchmark.