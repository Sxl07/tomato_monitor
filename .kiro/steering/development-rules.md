# Development Rules - Tomato Monitor

## Principios generales

- **Benchmark-first:** no reemplazar ni optimizar componentes sin métricas que lo justifiquen (ver ADR-003)
- **Spec-first:** no refactorizar capas completas sin una spec aprobada en `.kiro/specs/`
- **Cambios pequeños:** los cambios deben ser revisables, revertibles y aislados
- **Compatibilidad con RPi:** todo cambio debe poder ejecutarse en Raspberry Pi 5 con `DEVICE = "cpu"`

## Código

- No importar módulos de `legacy/` desde `src/` ni desde `app/`
- No agregar lógica de negocio en `app/routes/`; usar casos de uso en `src/application/use_cases/`
- No mezclar configuración hardcodeada en módulos de visión; usar `src/infrastructure/config/`
- No usar `torch.cuda.*` ni asumir GPU disponible
- Nuevas dependencias deben justificarse; preferir las ya en uso (PyTorch, OpenCV, numpy)

## Testing

- Smoke tests por componente van en `scripts/`
- Scripts de benchmark van en `scripts/benchmarks/` (crear si no existe)
- No hay suite de tests automatizados aún; al agregar una, usar `pytest`
- Toda prueba de rendimiento en RPi debe registrar temperatura, CPU y RAM

## Documentación

- Decisiones de arquitectura o tecnología relevantes → `docs/decisions/ADR-NNN-titulo.md`
- Resultados de benchmark → `docs/benchmarks/`
- Estado del proyecto → `docs/thesis-notes/current-state.md`
- Próximos pasos → `docs/thesis-notes/next-steps.md`

## Git

- Ramas por feature o spec: `feature/nombre` o `spec/001-nombre`
- Commits descriptivos en español o inglés, coherentes con el idioma del cambio
- No subir al repositorio: `.venv/`, `__pycache__/`, `outputs/` (sesiones generadas), videos grandes, modelos `.pth`
- No hacer `git add .` sin revisar el diff primero

## Flujo de specs

1. Definir spec en `.kiro/specs/{nombre}/requirements.md`
2. Diseño en `.kiro/specs/{nombre}/design.md`
3. Tareas en `.kiro/specs/{nombre}/tasks.md`
4. Ejecutar tareas secuencialmente, verificando build entre cada una
5. Documentar resultado en `docs/` o `docs/benchmarks/` según corresponda
