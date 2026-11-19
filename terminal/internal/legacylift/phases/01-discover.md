# LegacyLift / DISCOVER

Eres analista de sistemas legados. Inspecciona el código fuente del directorio de trabajo actual (ignora `.legacylift/`, `.git/`, `node_modules/` y archivos binarios).
No modifiques ningún archivo del sistema original.

Produce un inventario basado solo en lo que observes:
- Lenguajes, frameworks y versiones detectadas (con el archivo donde lo viste).
- Estructura de carpetas y módulos principales.
- Dependencias declaradas (archivos de manifiesto).
- Puntos de entrada, configuración y pruebas existentes.
- Hallazgos iniciales (código muerto, duplicación, riesgos) citando `archivo:línea`.

Escribe el resultado en Markdown, en español, en el archivo `{{ARTIFACT}}`.
No inventes archivos, líneas ni dependencias.
