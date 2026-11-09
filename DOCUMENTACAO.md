# LineScan RDT — Documentação Técnica

## Visão Geral

Sistema full-stack para análise de patologias em pavimentos asfálticos de rodovias brasileiras. O sistema processa imagens linescan capturadas ao longo das trilhas de roda, concatena os trechos em faixas contínuas, aplica modelos de visão computacional para detectar patologias e calcula o Índice de Gravidade Global (IGG) por quilômetro.

**Stack**: Python/FastAPI (backend) + React/TypeScript (frontend) + Redis/RQ (filas) + Docker Compose

---

## Arquitetura

```
┌─────────────┐     ┌──────────────┐     ┌─────────┐     ┌──────────┐
│  Frontend    │────▶│   Backend    │────▶│  Redis  │◀────│  Worker  │
│  React/Vite  │     │  FastAPI:8000│     │         │     │  rq      │
│  :5173       │     │              │     └─────────┘     └──────────┘
└─────────────┘     └──────────────┘                              │
                                                                  ▼
                                                          ┌──────────────┐
                                                          │   modelos/    │
                                                          │   dados/      │
                                                          └──────────────┘
```

- **Backend** — API REST, orquestração de trabalhos e persistência.
- **Worker** — executa os trabalhos em segundo plano (processamento de imagens e análise).
- **Redis** — fila de trabalhos (RQ).
- **Frontend** — interface web para configurar trechos, enviar dados e revisar análises.
- **PostgreSQL** — banco dos dados estruturados (em migração; ver tarefa RDT1-6). Sobe no compose
  com volume nomeado `postgres_data` e `healthcheck` via `pg_isready`.
- **Docker** — 5 serviços: `backend`, `worker`, `frontend`, `redis`, `postgres`.

---

## Fluxo de Operação

1. **Configuração do trecho** — o usuário informa nome, quilômetros inicial/final, sentido e tipo de pista.
2. **Envio de imagens** — por arquivo ZIP ou pasta (o envio é dividido em partes para suportar grandes volumes).
3. **Processamento** — as imagens são ordenadas e concatenadas em faixas contínuas de 5 m.
4. **Análise** — modelos de visão computacional detectam patologias (trincamento, couro de jacaré, panelas e remendos) sobre as faixas.
5. **Revisão** — o editor interativo permite visualizar, corrigir e complementar as detecções.
6. **IGG e exportação** — cálculo do IGG por quilômetro e exportação dos resultados.

---

## Módulos do Backend

| Módulo | Responsabilidade |
|---|---|
| `image_processor.py` | Processamento e concatenação de imagens linescan |
| `inference_pipeline.py` | Execução da análise de detecção de patologias |
| `model_loader.py` | Gerenciamento de modelos por tipo de análise |
| `igg_calculator.py` | Cálculo do Índice de Gravidade Global por quilômetro |
| `planilha_import.py` | Importação da planilha de afundamento das trilhas de roda (TRI/TRE) |
| `routes_folders.py` | API principal (trabalhos, análise, uploads) |
| `routes_reports.py` | Relatórios e exportação |
| `routes_uploads.py` | Upload de imagens (ZIP e por partes) |
| `jobs.py` | Definição dos trabalhos executados pelo worker |

---

## Detecções de Patologias

### Detecção automática

O sistema detecta automaticamente as seguintes patologias:

| Código | Patologia |
|---|---|
| T / TB | Trincas / Trinca em bloco |
| J / JE | Couro de jacaré (com/sem erosão) |
| P | Panela |
| R | Remendo |

### Rotulagem manual

As patologias abaixo não são detectadas automaticamente pelo sistema; elas podem ser marcadas manualmente no editor interativo quando necessário:

| Código | Patologia |
|---|---|
| EX | Exsudação |
| D | Desgaste |
| A | Afundamento |
| O | Ondulação |
| E | Escorregamento |

---

## Cálculo do IGG

O IGG é calculado por quilômetro conforme a metodologia de avaliação de pavimentos. A composição considera:

- Frequência das patologias por estação (grupos T1, FC-2, FC-3, O5, EX, D, R)
- Parâmetros de afundamento das trilhas de roda (TRI/TRE), importados de planilha ou preenchidos manualmente
- Item 9 — flecha média (limitada a 40)
- Item 10 — variância das flechas (limitada a 50)

**Conceitos**:

| Conceito | Faixa do IGG |
|---|---|
| Ótimo | IGG ≤ 20 |
| Bom | 20 < IGG ≤ 40 |
| Regular | 40 < IGG ≤ 80 |
| Ruim | 80 < IGG ≤ 160 |
| Péssimo | IGG > 160 |

---

## Execução

```bash
# Build e start
docker compose up -d --build

# Acessar
# Frontend: http://localhost:5173
# Backend:  http://localhost:8000
# Health:   http://localhost:8000/health
```

Requisitos: Docker com suporte a GPU (NVIDIA) para inferência acelerada. O volume `dados/` é criado em runtime para persistir as viagens processadas.

---

## Banco de dados

### Serviço

* `postgres:16-alpine`, container `rdt01-postgres`, `restart: unless-stopped`.
* Volume nomeado `postgres_data` em `/var/lib/postgresql/data`.
* Porta publicada `${POSTGRES_PORT:-5432}`.
* `healthcheck`: `pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"` (5 s de intervalo, 10 tentativas).
* `backend` e `worker` dependem de `postgres` com `condition: service_healthy`.

### Configuração

Credenciais e banco vêm do `.env` na raiz (modelo em `.env.example`): `POSTGRES_DB`,
`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_PORT`. O compose monta a `DATABASE_URL`
(`postgresql://<user>:<senha>@postgres:5432/<db>`) para backend e worker; o backend também a lê
via `Settings.database_url` (pydantic-settings). O `POSTGRES_PASSWORD` é obrigatório — sem ele o
compose falha com mensagem clara, evitando senha padrão commitada.

O endpoint `GET /health` devolve `database: true|false` como teste de conectividade (checagem
feita com `psycopg`).

### Camada de acesso e migrações

* `backend/src/db/` — SQLAlchemy 2.0: `base.py` (Base declarativa), `session.py` (engine, sessão,
  `session_scope` e dependência `get_session`), `models.py` (as 9 tabelas do schema da RDT1-8) e
  `repositories/` (projeto, viagem, bloco, faixa, detecção, parâmetro KM, job, resultado IGG/ICP,
  upload).
* Os repositórios **não** fazem `commit`: a transação é de `session_scope`/`get_session`. Assim,
  escritas por faixa/job ficam atômicas (sem JSON parcial).
* Migração inicial: `backend/migrations/versions/0001_schema_inicial.py`, que aplica o DDL
  autoritativo `backend/migrations/0001_schema_inicial.sql`.
* Comandos (em `backend/`, com `DATABASE_URL` definida):

  ```bash
  alembic upgrade head      # aplica o schema
  alembic downgrade base    # remove o schema (rollback)
  alembic current           # revisão aplicada
  ```

* Testes de banco (`tests/test_db_repositories.py`, `tests/test_migrations.py`) usam banco efêmero:
  com `DATABASE_TEST_URL` (ex.: o postgres do compose) ou `pgserver` local; sem nenhum, são pulados.

### Persistência de viagem/config/blocos/faixas (RDT1-11)

O primeiro slice vertical da migração (`backend/src/services/viagem_store.py`) coloca a
configuração da viagem, os blocos e as faixas no banco:

* `processar_imagens_job` grava `viagem_config` + todos os `lote_*_meta.json` **em uma única
  transação** depois da concatenação. A transação é idempotente (`projeto`/`nome` da viagem,
  `bloco.indice` e `faixa.arquivo`) e reconcilia o que saiu do conjunto com soft delete.
* Escritas concorrentes da mesma viagem são serializadas por advisory lock transacional; a
  criação do projeto também é serializada.
* A API lê configuração, faixas (`GET /api/pastas/faixas/{viagem}`) e metadados de bloco
  (`GET /api/pastas/meta/{viagem}/{bloco}`) do banco primeiro e cai no JSON legado quando a
  viagem ainda não está no banco.
* Durante a transição, `viagem_config.json` e `lote_*_meta.json` continuam sendo escritos e
  servem de fallback; se o banco estiver indisponível no worker, a concatenação não falha —
  apenas registra aviso e mantém o JSON.
* Enquanto não há gestão de projetos no produto, todas as viagens entram no projeto padrão
  `RDT01`.

Ainda pendente: persistir análise/detecções/parâmetros TRI-TRE (RDT1-12), histórico de
jobs e resultados IGG/ICP (RDT1-13) e a ETL dos `dados/` existentes (RDT1-14).

### Backup e restore

```bash
./scripts/db_backup.sh                       # grava backups/rdt01_<utc>.dump (pg_dump -Fc)
docker compose stop backend worker
./scripts/db_restore.sh backups/<arquivo>.dump
docker compose start backend worker
```

Os scripts rodam `pg_dump`/`pg_restore` via `docker compose exec`, sem exigir cliente PostgreSQL no
host. Restaure com backend/worker parados para não haver conexões abertas durante o `--clean`.
`backups/` está no `.gitignore`.

### Pendências

* O schema é aplicado por `alembic upgrade head` (manual/de deploy); o container ainda não roda a
  migração automaticamente na subida.
* Método do ICP flexível e retenção de jobs/resultados seguem em aberto na nota de schema (RDT1-8).