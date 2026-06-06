# Code Quality Steering

## Objetivo

El código de Tomato Monitor debe mantenerse claro, mantenible, testeable y adecuado para un proyecto de grado con despliegue en Raspberry Pi.

## Principios obligatorios

- Aplicar Clean Code.
- Aplicar principios SOLID cuando sean útiles y no generen sobreingeniería.
- Favorecer funciones pequeñas, nombres claros y responsabilidades únicas.
- Evitar duplicación innecesaria.
- Evitar lógica compleja sin explicación.
- Separar lógica de dominio, aplicación, infraestructura y presentación.
- Mantener cambios pequeños, revisables y trazables.
- No hacer refactorizaciones grandes sin spec aprobada.

## Reglas prácticas

- Una función debe tener una responsabilidad principal.
- Una clase no debe mezclar lógica de negocio, acceso a archivos, inferencia y presentación.
- Los módulos de infraestructura pueden depender de OpenCV, PyTorch, Detectron2 y archivos locales.
- Los módulos de dominio no deben depender de FastAPI, OpenCV, PyTorch ni archivos físicos.
- Evitar variables globales mutables.
- Evitar rutas hardcodeadas.
- Usar configuración centralizada cuando aplique.
- Agregar manejo explícito de errores en puntos de entrada, lectura de archivos, carga de modelos e inferencia.
- No ocultar errores críticos con `except Exception` vacío.
- Preferir código legible antes que micro-optimizaciones prematuras.

## Reglas para Kiro

- Antes de modificar código, identificar qué capa arquitectónica será afectada.
- Antes de crear una nueva clase o servicio, justificar su responsabilidad.
- No introducir patrones de diseño innecesarios.
- No cambiar comportamiento existente sin indicar impacto.
- Si una mejora es grande, crear primero una spec.