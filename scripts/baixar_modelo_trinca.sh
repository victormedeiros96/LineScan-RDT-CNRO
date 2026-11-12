#!/usr/bin/env bash
# Baixa o modelo de juntas do rígido (trinca-detector, GPL-3.0) do release
# público e valida o checksum. Uso:
#   ./scripts/baixar_modelo_trinca.sh [destino]
# Destino padrão: /opt/trinca-detector (monte esse diretório no container).
set -euo pipefail

DESTINO="${1:-/opt/trinca-detector}"
RELEASE="https://github.com/victormedeiros96/trinca-detector-public/releases/download/0.1"
MODELO="trinca_junta_limpo_ts.pt"
SHA256="901b70ad6c8995c20b1a32a61ae11562d3eb54c85f71e94034582a1b5590c3e6"

mkdir -p "$DESTINO/nuclio/modelos"

echo "Baixando código do trinca-detector..."
curl -fsSL "$RELEASE/trinca-detector-main.zip" -o /tmp/trinca-detector-main.zip
unzip -q -o /tmp/trinca-detector-main.zip -d "$DESTINO"

echo "Baixando modelo $MODELO..."
curl -fsSL "$RELEASE/$MODELO" -o "$DESTINO/nuclio/modelos/$MODELO"

echo "Conferindo checksum..."
echo "$SHA256  $DESTINO/nuclio/modelos/$MODELO" | sha256sum -c -

echo "OK: $DESTINO/nuclio/modelos/$MODELO"
echo "No compose, monte este diretório em /opt/trinca-detector (ICP_MODEL_DIR)."
