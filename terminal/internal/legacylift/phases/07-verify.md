# LegacyLift / VERIFY

Compara la versión candidata (`.legacylift/candidate/`) con el original (directorio actual, sin `.legacylift/`):
- Muestra el diff resumido por archivo (puedes usar `diff -ru` excluyendo `.legacylift`).
- Si el baseline de `.legacylift/05-baseline.md` tiene pruebas, ejecútalas sobre la candidata y compara resultados.
- Lista divergencias y un **confidence score orientativo** (0–100) explicando qué se verificó y qué **no**. Nunca afirmes equivalencia sin pruebas.

Escribe el informe de verificación en Markdown, en español, en `{{ARTIFACT}}`.
