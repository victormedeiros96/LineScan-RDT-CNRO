# LineScan RDT — Análise de Patologias de Pavimento

Sistema para análise de patologias em pavimentos asfálticos a partir de imagens linescan, com cálculo de Índice de Gravidade Global (IGG) e geração de relatórios.

## Visão geral

O sistema processa imagens capturadas ao longo da rodovia (trilha de roda), concatena os trechos em faixas contínuas e aplica modelos de visão computacional para detectar patologias. A análise é revisada em um editor interativo, com retigráfico, parâmetros de irregularidade (TRI/TRE) e exportação de resultados por quilômetro.

## Funcionalidades

- Envio de conjuntos de imagens por **ZIP ou pasta** (upload em partes, adequado para volume alto)
- Processamento em segundo plano com fila de trabalhos (concorrência, retomada e histórico)
- Detecção de patologias: trincamento, couro de jacaré, panelas e remendos
- Editor interativo com retigráfico, zoom, rotulação manual e correção de detecções
- Cálculo do **IGG** por quilômetro (conceito Ótimo/Bom/Regular/Ruim/Péssimo)
- Importação de planilha de afundamento das trilhas de roda (TRI/TRE)
- Exportação de resultados por trecho (CSV)

## Estrutura

```
backend/         # API (FastAPI, Python 3.12)
frontend/        # Interface web (React + TypeScript + Vite)
modelos/         # Modelos de visão computacional (por tipo de análise)
dados/           # Dados dos projetos (criado em runtime, montado como volume)
```

## Execução

```bash
cp .env.example .env   # ajuste POSTGRES_PASSWORD
docker compose up -d --build
```

- Frontend: http://localhost:5173
- Backend API: http://localhost:8000
- Health check: http://localhost:8000/health (inclui `database`)

## Banco de dados (PostgreSQL)

O serviço `postgres` usa a imagem `postgres:16-alpine`, persiste em um volume nomeado
(`postgres_data`) e expõe a porta `${POSTGRES_PORT:-5432}`. O `healthcheck` usa `pg_isready`;
`backend` e `worker` só sobem depois de o banco estar saudável.

As credenciais vêm do `.env` (veja `.env.example`); o `docker compose` monta a
`DATABASE_URL` do backend e do worker automaticamente. Nenhum segredo é commitado.

### Backup e restore

```bash
# backup lógico (formato custom, comprimido) em backups/
./scripts/db_backup.sh

# restore: pare backend/worker antes para evitar conexões abertas
docker compose stop backend worker
./scripts/db_restore.sh backups/rdt01_<timestamp>.dump
docker compose start backend worker
```

Os scripts executam `pg_dump`/`pg_restore` dentro do container, então não é preciso ter
cliente PostgreSQL instalado no host. `backups/` está no `.gitignore`.

## Requisitos

- Docker com suporte a GPU (NVIDIA) para inferência acelerada
- Volume `dados/` para persistência das viagens processadas