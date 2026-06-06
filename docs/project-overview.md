# Tomato Monitor - Project Overview

## Descripción general

Tomato Monitor es un sistema de monitoreo visual para tomates cherry orientado a ejecución edge en Raspberry Pi. El proyecto integra una aplicación FastAPI, modelos de visión por computador, persistencia local y un pipeline de análisis visual.

## Objetivo general

Desarrollar y evaluar un sistema de monitoreo visual de tomates cherry capaz de detectar frutos, clasificar su sanidad visual y apoyar la estimación de madurez mediante procesamiento de imágenes, con orientación a despliegue en Raspberry Pi.

## Estado actual

- La aplicación corre en PC de escritorio y portátil.
- La aplicación ya fue levantada en Raspberry Pi 5.
- FastAPI funciona correctamente en Raspberry.
- Detectron2 fue instalado correctamente usando `--no-build-isolation`.
- El modelo de inferencia fue probado.
- El pipeline actual ejecuta detección, crops, snapshots y reconstrucción de video.
- El consumo máximo observado de RAM fue aproximadamente 2.1 GB.
- El cuello de botella principal identificado es CPU/temperatura, no RAM.

## Restricción principal

El sistema debe evaluarse en Raspberry Pi 5, por lo que toda decisión técnica debe considerar CPU, RAM, temperatura, almacenamiento, cámara y estabilidad.