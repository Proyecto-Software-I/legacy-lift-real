# LegacyLift / PLAN

Lee los artefactos de `.legacylift/` (en especial `{{PREV}}`).
Genera un **Modernization Blueprint** en español:
- Arquitectura actual y arquitectura objetivo.
- Estrategia por unidad, alcance y fuera de alcance.
- Riesgos y priorización.
- **Una sola Migration Wave** ejecutable: lista exacta de archivos a cambiar, el cambio esperado en cada uno y sus dependencias.
- Criterios de verificación de la wave.

Mantén la wave pequeña y segura (refactor limitado dentro del mismo lenguaje). Escribe el resultado en Markdown en `{{ARTIFACT}}`.
