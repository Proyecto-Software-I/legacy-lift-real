# LegacyLift / BASELINE

Antes de modernizar, registra el comportamiento actual del sistema original en el directorio actual (sin modificarlo):
- Detecta pruebas existentes y cómo se ejecutan. Si es seguro (sin red, sin borrar archivos), ejecútalas y registra el resultado.
- Si no hay pruebas, deja indicado **"sin baseline de pruebas: no se podrá afirmar equivalencia"** y propón casos de entrada/salida para los archivos de la wave de `{{PREV}}`.

Escribe el resultado en Markdown, en español, en `{{ARTIFACT}}`.
