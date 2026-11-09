#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ $# -ne 1 ]]; then
    echo "Uso: $0 <arquivo.dump>" >&2
    exit 1
fi

file="$1"
if [[ ! -f "$file" ]]; then
    echo "Arquivo não encontrado: ${file}" >&2
    exit 1
fi

docker compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --no-privileges' < "$file"

echo "Restauração concluída a partir de ${file}"
