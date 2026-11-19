#!/bin/sh
# Instala la última versión de la terminal de LegacyLift (macOS y Linux).
# Uso: curl -fsSL https://raw.githubusercontent.com/Proyecto-Software-I/legacy-lift-real/main/terminal/scripts/install-legacylift.sh | sh
set -eu
REPO="Proyecto-Software-I/legacy-lift-real"
os=$(uname -s | tr '[:upper:]' '[:lower:]')
case "$(uname -m)" in x86_64|amd64) arch=amd64 ;; arm64|aarch64) arch=arm64 ;; *) echo "Arquitectura no soportada"; exit 1 ;; esac
version=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" | sed -n 's/.*"tag_name": *"v\{0,1\}\([^"]*\)".*/\1/p' | head -1)
[ -n "$version" ] || { echo "No se encontró ninguna versión publicada"; exit 1; }
tmp=$(mktemp -d)
curl -fsSL "https://github.com/$REPO/releases/download/v$version/legacylift_${version}_${os}_${arch}.tar.gz" | tar -xz -C "$tmp"
dest="${LEGACYLIFT_BIN:-$HOME/.local/bin}"
mkdir -p "$dest" && mv "$tmp/legacylift" "$dest/legacylift" && chmod +x "$dest/legacylift"
echo "LegacyLift $version instalado en $dest/legacylift"
case ":$PATH:" in *":$dest:"*) ;; *) echo "Agrega $dest a tu PATH para usar el comando legacylift." ;; esac
