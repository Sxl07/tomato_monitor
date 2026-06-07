# Comparación de perfiles: Edge vs Full

**Estado:** PENDIENTE — Ejecutar en Raspberry Pi 5 con refrigeración activa.

## Dispositivo
- Hardware: Raspberry Pi 5 (8 GB)
- OS: Raspberry Pi OS / Debian Bookworm 64-bit
- Refrigeración: Case oficial con ventilador activo
- Commit: _PENDIENTE_
- Fecha: _PENDIENTE_

## Comando de ejecución
```bash
python scripts/benchmarks/benchmark_edge_profiles.py --images data/images/ --output docs/benchmarks/edge-profile-comparison.md
```

## Resultados

### Perfil: edge
| Métrica | Valor |
|---|---|
| Resolución de inferencia | 416×312 |
| Skip madurez | Sí |
| Threshold detección | 0.85 |
| Cooldown Scene Gate | 30 frames |
| Tiempo inferencia promedio | _PENDIENTE_ |
| RSS pico | _PENDIENTE_ |
| Temperatura pico | _PENDIENTE_ |
| Throughput | _PENDIENTE_ snapshots/min |

### Perfil: full
| Métrica | Valor |
|---|---|
| Resolución de inferencia | 640×480 |
| Skip madurez | No |
| Threshold detección | 0.80 |
| Cooldown Scene Gate | 18 frames |
| Tiempo inferencia promedio | _PENDIENTE_ |
| RSS pico | _PENDIENTE_ |
| Temperatura pico | _PENDIENTE_ |
| Throughput | _PENDIENTE_ snapshots/min |

## Comparación
| Métrica | Edge | Full | Diferencia |
|---|---|---|---|
| Tiempo inferencia | _PENDIENTE_ | _PENDIENTE_ | _PENDIENTE_ |
| Temperatura pico | _PENDIENTE_ | _PENDIENTE_ | _PENDIENTE_ |
| Throughput | _PENDIENTE_ | _PENDIENTE_ | _PENDIENTE_ |

## Conclusiones
_PENDIENTE: Documentar conclusiones después de ejecutar el benchmark._

## Decisiones derivadas
- ADR-005: Perfiles de ejecución edge vs full
