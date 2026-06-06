# Security Steering

## Objetivo

El proyecto debe seguir buenas prácticas de ciberseguridad y seguridad de la información, considerando que funcionará en una Raspberry Pi y puede manejar archivos, cámara, modelos, resultados y una aplicación web local.

## Principios base

Usar el modelo NIST 2.0, TOP TEN OWASP y principios de seguridad de la informacion como guía.

## Reglas de confidencialidad

- No hardcodear secretos, tokens, contraseñas ni rutas privadas.
- No registrar información sensible en logs.
- No exponer endpoints innecesarios.
- No asumir que la red local es confiable.
- Usar variables de entorno o archivos `.env` ignorados por Git para configuración sensible.
- No subir `.env`, credenciales, claves ni archivos privados a GitHub.

## Reglas de integridad

- Validar entradas de usuario, rutas de archivos y nombres de archivos.
- Evitar path traversal en cargas o selección de archivos.
- Validar existencia y tipo de archivos antes de procesarlos.
- Registrar versión de modelos usados en benchmarks.
- Evitar sobrescribir resultados sin control.
- Mantener trazabilidad de resultados mediante timestamp, commit y configuración usada.

## Reglas de disponibilidad

- Evitar procesos largos bloqueando la API sin control.
- Evitar hooks que ejecuten inferencias pesadas automáticamente.
- Controlar errores de carga de modelos y cámara.
- Manejar fallos de archivo, cámara, memoria y temperatura.
- No ejecutar pruebas pesadas sin monitoreo térmico en Raspberry Pi.
- Mantener refrigeración activa como requisito para pruebas largas.

## Reglas para Kiro

- Al crear endpoints FastAPI, validar entradas y errores.
- Al crear scripts, evitar rutas destructivas.
- Al tocar persistencia, cuidar integridad de CSV/resultados.
- Al tocar cámara o pipeline, considerar fallos y recuperación.
- No agregar dependencias de seguridad sin justificación.