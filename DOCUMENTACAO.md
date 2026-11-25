# LineScan RDT — Documentação Técnica

## Visão Geral

Sistema full-stack para análise de patologias em pavimentos asfálticos de rodovias brasileiras. O sistema processa imagens linescan capturadas ao longo das trilhas de roda, concatena os trechos em faixas contínuas, aplica modelos de visão computacional para detectar patologias e calcula o Índice de Gravidade Global (IGG) por quilômetro.

**Stack**: Python/FastAPI (backend) + React/TypeScript (frontend) + Redis/RQ (filas) + Docker Compose

---

## Arquitetura

```
┌─────────────┐     ┌──────────────┐     ┌─────────┐     ┌──────────┐
│  Frontend    │────▶│   Backend    │────▶│  Redis  │◀────│  Worker  │
│  React/Vite  │     │  FastAPI:8000│     │  fila   │     │  rq      │
│  :5173       │     │              │     └─────────┘     └──────────┘
└─────────────┘     └──────┬───────┘                              │
                           │                                      │
                           ▼                                      ▼
                    ┌─────────────────────────────────────────────────┐
                    │              PostgreSQL (rdt01)                 │
                    │  projeto/viagem/bloco/faixa/deteccao/job/...    │
                    └─────────────────────────────────────────────────┘
                           ▲                                      ▲
                           │                                      │
                    ┌──────┴───────┐                      ┌───────┴──────┐
                    │   modelos/   │                      │  dados/      │
                    │  (read-only) │                      │  (legado/    │
                    │              │                      │  fallback)   │
                    └──────────────┘                      └──────────────┘
```

- **Backend** — API REST, orquestração de trabalhos e persistência.
- **Worker** — executa os trabalhos em segundo plano (processamento de imagens e análise).
- **Redis** — fila de trabalhos (RQ). Não é fonte de verdade: o histórico de jobs e os
  resultados vivem no PostgreSQL (RDT1-13).
- **Frontend** — interface web para configurar trechos, enviar dados e revisar análises.
- **PostgreSQL** — fonte de verdade dos dados estruturados (schema da RDT1-8, aplicado por
  migração Alembic). Sobe no compose com volume nomeado `postgres_data` e `healthcheck` via
  `pg_isready`.
- **`dados/`** — artefatos legados por viagem, mantidos durante a transição como fallback/exportação
  e importados pela ETL (RDT1-14). Ver [Dados e Artefatos em Disco](#dados-e-artefatos-em-disco).
- **Docker** — 5 serviços: `backend`, `worker`, `frontend`, `redis`, `postgres`.

A operação (subir banco, migrar, backup/restore, cutover e rollback) está no
[Guia de Execução](#operação--guia-de-execução).

---

## Fluxo de Operação

1. **Configuração do trecho** — o usuário informa nome, quilômetros inicial/final, sentido e tipo de pista.
2. **Envio de imagens** — por arquivo ZIP ou pasta (o envio é dividido em partes para suportar grandes volumes).
3. **Processamento** — as imagens são ordenadas e concatenadas em faixas contínuas de 5 m.
4. **Análise** — modelos de visão computacional detectam patologias (trincamento, couro de jacaré, panelas e remendos) sobre as faixas.
5. **Revisão** — o editor interativo permite visualizar, corrigir e complementar as detecções.
6. **IGG e exportação** — cálculo do IGG por quilômetro e exportação dos resultados.

---

## Dados e Artefatos em Disco

A migração para banco (RDT1-6) moveu a fonte de verdade dos dados estruturados para o
PostgreSQL. Os arquivos em `dados/` continuam existindo durante a transição: são
fallback de leitura, exportação/compatibilidade e insumo da ETL (RDT1-14). A tabela
abaixo resume onde cada dado vive hoje.

| Dado | Fonte de verdade | Artefato legado em `dados/` | Observação |
|---|---|---|---|
| Projeto e viagem (config) | `projeto`, `viagem` | `<viagem>/viagem_config.json` | Vocabulário legado `tipo_faixa`/`duplicada` normalizado na ETL |
| Blocos e faixas | `bloco`, `faixa` | `<viagem>/lote_*_meta.json` | Faixas concatenadas gravadas pelo job de concatenação |
| Detecções | `deteccao` | `<viagem>/analise_completa.json` | JSON ainda escrito uma vez ao fim do job, para exportação |
| Parâmetros TRI/TRE | `parametro_km` | `<viagem>/parametros_adicionais.json` | Séries TRI/TRE + `extras` |
| Jobs/histórico | `job` | — (legado vive no Redis) | Redis é só a fila; ETL não importa jobs antigos |
| Resultados IGG/ICP | `resultado_analise` | — | `detalhe` em JSONB; ICP por amostra (`amostra` na chave) calculado por `icp_calculator.py` |
| Imagens e artefatos de modelo | Volume `dados/` / `modelos/` | `<viagem>/...`, `modelos/` | `modelos/` é montado read-only |

- **Volume `dados/`** — criado em runtime e montado em `backend` e `worker`. Está no
  `.gitignore`; nunca é versionado.
- **Volume `modelos/`** — montado read-only em `:ro`; contém os modelos por tipo de análise.
- **Volume nomeado `postgres_data`** — dados do PostgreSQL, gerenciado pelo Docker; não é
  um diretório do host.
- **Backups** — `backups/rdt01_<utc>.dump` (formato custom do `pg_dump`), também no
  `.gitignore`. Não versionar backups nem dados.

Os artefatos legados **não** são removidos pelo cutover: o banco passa a ser autoritativo e
os arquivos ficam apenas como fallback/exportação, até que uma tarefa futura os descontinue.
Nenhuma migração ou rollback apaga arquivos de `dados/` — o rollback do banco é feito por
soft delete e/ou restore do backup (ver abaixo).

---

## Módulos do Backend

| Módulo | Responsabilidade |
|---|---|
| `image_processor.py` | Processamento e concatenação de imagens linescan |
| `inference_pipeline.py` | Execução da análise de detecção de patologias |
| `model_loader.py` | Leitura das configurações de modelo por tipo de análise |
| `model_lifecycle.py` | Ciclo de vida dos modelos: lazy load, reuso e descarga ociosa (RDT1-16) |
| `igg_calculator.py` | Cálculo do Índice de Gravidade Global por quilômetro |
| `icp_calculator.py` | Cálculo do ICP de pavimento rígido (método da concessionária) |
| `icp_curvas.py` | Curvas e correções do ICP geradas das planilhas ICP Pedágio |
| `icp_blocos.py` | Fecha os blocos do ICP cruzando transversais × longitudinais e completa juntas virtuais (RDT1-33) |
| `icp_import.py` | Importação das fichas de inspeção ICP (XLSX/CSV) e cálculo por amostra (RDT1-17) |
| `icp_trecho.py` | ICP do trecho por inspeção total/amostragem e recálculo de `n` (RDT1-25) |
| `icp_laudo.py` | Laudo do ICP com as seções do item 5.5 e amostras com ICP ≤ 40 (RDT1-26) |
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

### Ciclo de vida dos modelos (RDT1-16)

Os modelos de visão (`.pt` do Ultralytics e `.onnx` do ONNX Runtime) são
gerenciados por um `ModelManager` por processo (`backend` e `worker` têm o seu),
em `backend/src/services/model_lifecycle.py`:

* **Lazy load** — nada é carregado no startup; a primeira inferência dispara o
  carregamento.
* **Reuso** — enquanto um modelo está em uso (inferência em andamento) o mesmo
  objeto é reaproveitado entre janelas e jobs; cada uso segura uma alça com
  refcount, então o modelo só é descarregado quando o refcount volta a zero.
* **TTL ocioso** — sem uso por `MODEL_IDLE_TTL_SECONDS` (padrão `300`, 5 min;
  `0` desabilita), uma thread de manutenção descarrega o modelo: libera a
  sessão ONNX (`close()`), faz `del`/`gc.collect()` e, havendo CUDA,
  `torch.cuda.empty_cache()`.
* **Observabilidade** — logs ao carregar/descarregar (com VRAM alocada/reservada
  quando o contexto CUDA já existe) e `GET /debug/modelos`, que devolve os
  modelos carregados, refcount, segundos ociosos e métricas de VRAM. O endpoint
  não inicializa CUDA por conta própria (mantém o lazy load).

A concorrência é protegida por um lock por processo: a descarga nunca ocorre com
refcount > 0, e o carregamento é serializado. Não é necessário forçar
`torch.cuda.ipc_collect()` no cenário atual (processo único por container).

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

## Cálculo do ICP (pavimento rígido) — método da concessionária

O cálculo fica em `backend/src/services/icp_calculator.py`; as curvas e correções
ficam em `icp_curvas.py`, transcritas das fórmulas fechadas das planilhas
**ICP Pedágio (Com Fórmula)** (Anexo II.1–II.9 e PPV01), repassadas pela
concessionária no card RDT1-3. O motor é por **amostra** (até 20 placas) e foi
travado por regressão com **108/108 amostras** dessas planilhas em
`backend/tests/test_icp_calculator.py` (fixture em `backend/tests/fixtures/`).

A base normativa é a DNIT 062/2004-PRO (CERL, 1979), mas o cálculo implementa de
propósito as escolhas da planilha, que diferem da leitura estrita da norma:

- `q` conta os tipos (exceto o 6.5) cujo maior VD é **`>= 5`** e não conta o 6.5;
- `VDC = max(maior correção "q1 por defeito", curva q do VDT)`;
- `q = 0` → `VDC = VDT`; `VDT = 0` → `ICP = 100`;
- o 6.18 usa a curva da planilha (em A/M é praticamente a 6.4 do PDF);
- o defeito 6.5 conta só a maior severidade presente.

Consequência: a ficha de exemplo da norma fecha em **ICP 52,23** (VDC 47,77), não
em 52,0. O ICP do **flexível** continua em aberto; esta norma é só para rígido.

**Conceitos** (limites inferiores exclusivos, como na planilha):

| Conceito | Faixa do ICP |
|---|---|
| Destruído | ICP < 10 |
| Muito Ruim | 10 ≤ ICP < 25 |
| Ruim | 25 ≤ ICP < 40 |
| Razoável | 40 ≤ ICP < 55 |
| Bom | 55 ≤ ICP < 70 |
| Muito Bom | 70 ≤ ICP < 85 |
| Excelente | 85 ≤ ICP ≤ 100 |

### Importação da ficha e API (RDT1-17)

A ficha de inspeção (planilhas **ICP Pedágio (Com Fórmula)** / PPV01) entra por
`POST /api/relatorios/icp/{viagem}` (multipart `file`). O importador em
`backend/src/services/icp_import.py` lê cada aba `ICP_n` e:

1. usa o **R/U digitado manualmente** (nº de placas afetadas / percentual) quando
   presente — é o que a planilha exibe;
2. caso contrário, conta os **códigos C/I** (`tipo + severidade`, ex.: `2B`,
   `10`) das colunas `C:E` e `I:K`;
3. converte a contagem em densidade (`placas afetadas / Nº Placas`) e chama
   `calcular_icp_amostra`, devolvendo VD por defeito, VDT, q, VDC, ICP e conceito.

Também é aceito **CSV achatado** (`amostra`, `total_placas`, `tipo`,
`severidade` e `placas_afetadas` ou `densidade`, por linha; `km`, `rodovia`,
`faixa`, `data` e `operador` opcionais). O `.xls` deve ser salvo como `.xlsx`.

Cada amostra é persistida em `resultado_analise` com `tipo_analise = 'icp'` e
`amostra = 'ICP_n'`; o KM vem do campo `INICIAL` da planilha (`33+600` → 33).
Amostras sem KM (PPV01) usam `km = 0` como sentinela e `detalhe.km_informado = false`.
Várias amostras podem compartilhar o mesmo KM (as faixas de uma praça); por isso
a chave única da tabela inclui `amostra` (migração `0002_resultado_amostra`).
Reimportar a mesma ficha atualiza as amostras e reconcilia com soft delete as que
saíram do arquivo.

O resultado fica consultável em:

* `GET /api/relatorios/icp/{viagem}?km=33` — amostras persistidas (o campo
  `valor`/`icp` é o ICP);
* `GET /api/relatorios/resultados/{viagem}?tipo=icp` — visão genérica por KM.

Sem banco disponível, o `POST` continua devolvendo o cálculo completo; a
persistência é *best-effort* (o `GET` responde 503).

Regressão: `backend/tests/test_icp_import.py` remonta as planilhas da fixture e
confere **108/108 ICPs**; `test_api_icp.py` cobre upload, cálculo e `GET`.

### Cálculo do ICP do trecho (RDT1-25)

O nível do trecho fica em `backend/src/services/icp_trecho.py`
(`calcular_icp_trecho`) e segue a **DNIT 062/2004-PRO**, itens 6 e 7.3:

* **inspeção total** (item 7.3.1) — `ICPt` = média aritmética dos ICPs das
  amostras; é o padrão, porque a RDT1-21 processa o trecho em contínuo;
* **amostragem sem adicionais** (item 7.3.2) — mesma média aritmética;
* **amostragem com amostras adicionais** (item 7.3.3) —
  `ICPt = ((N − A) / N)·ICP1 + (A / N)·ICP2`, com `ICP1` a média das amostras
  normais, `ICP2` a média das adicionais, `N` o total de amostras do trecho e
  `A` a quantidade de adicionais;
* **número mínimo de amostras** (item 6) —
  `n = (4·N·S²) / (e²·(N − 1) + 4·S²)`, com `S` o desvio padrão **amostral**
  dos ICPs (`n − 1` no denominador) e `e` o erro admissível informado.

Decisões (card RDT1-25):

* `e` **não tem valor padrão** — a norma não fixa um. Sem `e`, o ICPt e `S`
  são calculados e `n` fica indisponível (`n_disponivel = false`); com `e`, ele
  precisa ser finito e maior que zero. `n` é arredondado para cima e nunca é
  menor que 1 (se `S = 0` a fórmula zera, mas o trecho já tem amostras).
* `N` deriva do inventário/persistência: é a quantidade de amostras com ICP. O
  parâmetro pode ser repetido na chamada apenas para conferência.
* A estratégia de adicionais é explícita: o operador informa os ids das
  amostras adicionais (`adicionais`); é preciso haver ao menos uma normal e uma
  adicional.
* O conceito usa o Anexo B na mesma tabela do cálculo por amostra
  (`classificar_icp`), sem nova faixa.
* Amostra em branco/sem defeitos continua chegando com `ICP = 100` (VDT 0) e
  entra normalmente na média.

A consolidação lê as amostras **persistidas** — ficha importada mais as
marcações manuais já recalculadas por `icp_marcacoes` — para manter uma única
base. A API fica em `POST /api/relatorios/icp/{viagem}/trecho`, com corpo
`{ "metodo", "adicionais", "N", "e" }`; a resposta é
`{ status, viagem, persistido, trecho }`. O resultado também é consultável em
`GET /api/relatorios/icp/{viagem}/trecho`.

Persistência: o trecho usa `resultado_analise` com `amostra IS NULL` e o KM
sentinela 0 (`detalhe.nivel = "trecho"` guarda `km_inicio`/`km_fim`), mantendo a
chave única `(viagem, km, tipo_analise, amostra)` e a gravação idempotente. O
`GET .../icp/{viagem}` lista só as amostras; reimportar a ficha reconcilia
apenas as amostras (`amostra IS NOT NULL`), sem apagar o consolidado do trecho.
O CSV do `GET .../icp/{viagem}/exportar` ganhou as colunas `N`, `N_MINIMO`,
`DESVIO_PADRAO`, `E`, `METODO` e uma linha final `TRECHO`.

Regressão: `backend/tests/test_icp_trecho.py` cobre o exemplo da norma
(ICP 52,23 / Razoável), a fórmula 7.3.3, `S`/`n` conferidos à mão, os casos de
`e` ausente/inválido e a persistência com `amostra` nula;
`test_persistencia_jobs.py` garante que a reconciliação das amostras preserva o
trecho.


### Laudo do ICP (RDT1-26)

O laudo do item 5.5 da **DNIT 062/2004-PRO** é montado por
`backend/src/services/icp_laudo.py` (`montar_laudo`) a partir das amostras e do
consolidado persistidos. Ele reúne:

* **resumo do trecho** — ICPt, conceito, `N`, desvio padrão `S`, `e` e `n`
  mínimo (usa o trecho persistido; se não houver, consolida pela inspeção total
  do item 7.3.1);
* **distribuição por conceito** do Anexo B (quantidade, percentual e faixa de
  ICP observada, sem alterar as faixas de `classificar_icp`);
* **resumo por amostra** (VDT, `q`, VDC, ICP e conceito);
* **juntas e selante** (defeito 5) com severidade, densidade e extensão
  *estimada* (`nº de juntas afetadas × largura mediana da placa` do inventário
  `icp_placas.json`; sem inventário, `extensao_m` fica nulo);
* **defeitos atípicos** — registros fora das curvas 6.1–6.18 (sem valor
  dedutível) e o campo opcional `atipicos` da amostra;
* **assentamentos** e **buracos** — itens 19 e 20, registros complementares
  não dedutíveis;
* **amostras com ICP ≤ 40** (Anexo B: "Ruim" e pior), inclusive o limite 40.

Amostra **em branco** (sem defeitos, ICP 100) aparece no resumo por amostra e na
distribuição de conceitos, mas **não** entra nas seções de defeitos.

API:

* `GET /api/relatorios/icp/{viagem}/laudo` — JSON estruturado com
  `metadados`, `resumo_trecho`, `distribuicao_conceitos`, `amostras`,
  `defeitos`, `amostras_criticas` e `secoes`;
* `GET /api/relatorios/icp/{viagem}/laudo.csv` — laudo em CSV com blocos de
  metadados, resumo do trecho, resumo por amostra, uma linha por defeito,
  juntas/selante, atípicos, assentamentos, buracos, ICP ≤ 40 e distribuição
  por conceito;
* o `GET .../icp/{viagem}/exportar` continua sendo a planilha normalizada (uma
  linha por defeito + linha `TRECHO`), agora complementada pelo laudo.

Na tela (`frontend/src/components/IcpView.tsx`) o card **Laudo** mostra o
resumo do trecho, a distribuição de conceitos e filtros por seção (defeitos,
juntas/selante, atípicos, assentamentos, buracos, ICP ≤ 40) e por amostras
críticas; dois botões exportam a planilha e o laudo.

Regressão: `backend/tests/test_icp_laudo.py` (seções, amostra em branco,
limite `ICP = 40`, extensão das juntas, trecho persistido) e
`test_api_icp.py` (JSON e CSV do laudo).

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

## Operação — Guia de Execução

Runbook do banco relacional: subir, migrar, importar o legado, monitorar, diagnosticar,
fazer o cutover e reverter. Os comandos assumem a raiz do repositório e o `.env`
configurado (veja [Variáveis de ambiente e segredos](#variáveis-de-ambiente-e-segredos)).

### 1. Subir o banco

```bash
cp .env.example .env          # se ainda não existir; ajuste POSTGRES_PASSWORD
docker compose up -d postgres
docker compose ps postgres    # STATUS deve ficar "healthy"
docker compose logs -f postgres
```

O `healthcheck` usa `pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"`; `backend` e
`worker` só sobem depois de o banco estar saudável. Suba a stack completa com
`docker compose up -d --build`.

### 2. Rodar as migrações

O schema é aplicado por Alembic (`backend/migrations/versions/0001_schema_inicial.py`,
que executa o DDL de `backend/migrations/0001_schema_inicial.sql`). O container **não**
migra sozinho na subida: rode o comando de forma explícita após subir o banco.

```bash
# Com a stack de pé (DATABASE_URL já vem do compose):
docker compose exec backend alembic current        # revisão aplicada (vazio = nenhuma)
docker compose exec backend alembic upgrade head   # aplica o schema
docker compose exec backend alembic history        # histórico de revisões

# Fora do compose, em backend/ com DATABASE_URL definida:
alembic upgrade head
alembic current
```

- `alembic downgrade base` **remove todas as tabelas de domínio** — use só em banco de
  desenvolvimento/teste ou quando o restore de backup for a opção (ver rollback).
- Se as tabelas já existirem por DDL manual sem `alembic_version`, o `upgrade head`
  falha com "relation already exists". Confira se o schema bate com a revisão e só então
  alinhe o versionamento com `alembic stamp head`; na dúvida, recrie o banco vazio.
- Testes de migração (`backend/tests/test_migrations.py`) aplicam e revertem o schema em
  banco efêmero, com `DATABASE_TEST_URL` ou `pgserver`.

### 3. Importar os dados legados (ETL)

Sem janela de manutenção, o `--dry-run` pode rodar a qualquer momento: ele só lê
`dados/` e imprime contagens/checksums. A execução real grava no banco e gera o
relatório (manifesto de rollback).

```bash
# 1) simulação (não grava)
docker compose exec backend python -m src.migrar_dados_legados --dry-run

# 2) execução real, gravando o relatório no volume dados/ (visível no host)
docker compose exec backend python -m src.migrar_dados_legados \
    --report /app/dados/migracao_relatorio.json
```

Rode o `--dry-run` de novo após a execução real: contagens e `checksum_consolidado`
devem ser os mesmos (idempotência). O relatório e o rollback detalhado estão em
[ETL dos dados legados (RDT1-14)](#etl-dos-dados-legados-rdt1-14).

### 4. Backup e restore (antes do cutover)

Sempre faça o backup **antes** do cutover e de qualquer execução real da ETL:

```bash
./scripts/db_backup.sh          # backups/rdt01_<utc>.dump (pg_dump -Fc)

docker compose stop backend worker
./scripts/db_restore.sh backups/rdt01_<utc>.dump
docker compose start backend worker
```

Detalhes e restrições em [Backup e restore](#backup-e-restore). `backups/` é ignorado
pelo git. O restore é o rollback autoritativo do cutover (seção 8).

### 5. Monitorar

```bash
curl -s http://localhost:8000/health        # {"status":"ok",...,"database":true}
docker compose ps                           # postgres "healthy", backend/worker "running"
docker compose logs --tail=200 backend worker | grep -iE "banco|database|erro|error"
```

Consultas úteis (o `sh -c` deixa o container expandir `POSTGRES_USER`/`POSTGRES_DB`):

```bash
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
  select status, count(*) from job group by status order by 1;
  select count(*) filter (where deleted_at is null) as viagens_ativas from viagem;
  select count(*) from deteccao where deleted_at is null;
  select max(created_at) as ultimo_job from job;
"'
```

- `GET /health` retorna `database: false` quando o `DATABASE_URL` está ausente ou a
  conexão falha (checagem via `psycopg`).
- Jobs finalizados antes de `JOB_RETENTION_DAYS` (padrão 30) recebem soft delete ao fim
  de cada job; contagens ativas ignoram `deleted_at`.
- Sem banco disponível, as operações de persistência são *best-effort* (logam aviso) e a
  API/worker seguem com o fallback legado — o serviço não cai, mas nada novo é persistido.

### 6. Diagnosticar

| Sintoma | Causa provável | Ação |
|---|---|---|
| `docker compose` falha com "defina POSTGRES_PASSWORD" | `.env` ausente ou senha vazia | Criar/ajustar `.env` a partir de `.env.example` |
| `postgres` não fica `healthy` | Porta/volume/credenciais | `docker compose logs postgres`; conferir `POSTGRES_*` e `postgres_data` |
| `/health` com `database: false` | `DATABASE_URL` ausente/errada ou banco fora | Conferir env do backend, `docker compose ps`, conectividade |
| `alembic upgrade` erro "relation already exists" | Tabelas criadas fora do Alembic | Conferir schema; alinhar com `alembic stamp head` ou recriar vazio |
| `alembic upgrade` erro de conexão | `DATABASE_URL` ausente | Rodar dentro do compose ou exportar a variável |
| ETL real sem efeito, log "Banco indisponível" | Banco fora/URL errada | Subir banco e repetir; o dry-run funciona sem banco |
| Contagens da ETL maior que o esperado | Já havia dados sem soft delete | Conferir `checksum_consolidado` e duplicatas pelas chaves naturais |
| Job preso em `queued`/`started` | Worker parado/travado | `docker compose ps worker`; logs; reiniciar worker |
| Escrita esperando (advisory lock) | Outra transação na mesma viagem | Aguardar; investigar jobs concorrentes da mesma viagem |

### 7. Plano de cutover

Objetivo: passar a fonte de verdade para o PostgreSQL sem perder nem duplicar dados.
Faça em **janela de manutenção** (sem uploads/análises em andamento), com o responsável
técnico acompanhando.

**Ordem**

1. Congelar escritas: aguardar os jobs em execução terminarem e parar `worker` e `backend`.
2. Backupear o banco atual: `./scripts/db_backup.sh` (guarde o arquivo e o horário).
3. Subir/atualizar o banco e aplicar o schema: `alembic upgrade head`;
   conferir `alembic current` = `0001_schema_inicial` e as 9 tabelas + `alembic_version`.
4. Rodar a ETL em `--dry-run` e guardar as contagens/checksum.
5. Rodar a ETL real com `--report`, guardando o relatório (manifesto de rollback).
6. Validar (checklist abaixo). Se falhar, **rollback** (seção 8).
7. Religar `backend` e `worker`, fazer um smoke test de leitura/escrita.
8. Monitorar `health`, logs e contagens por pelo menos um ciclo de job.

**Validação / critérios de sucesso**

- `alembic current` no `head` e `GET /health` com `database: true`.
- ETL real conclui sem erro; `--dry-run` após a execução real devolve **as mesmas
  contagens e o mesmo `checksum_consolidado`** (idempotência, sem duplicação).
- Contagens batem com o dry-run e com o backup (viagens/blocos/faixas/detecções/parâmetros).
- Sem violação das chaves únicas naturais (a própria migração falharia em caso de duplicata).
- `GET /api/pastas/analise/{viagem}` e `GET /api/pastas/faixas/{viagem}` leem do banco
  (viagem presente no banco, sem cair no JSON legado) e amostras conferem com `dados/`.
- Histórico (`GET /api/pastas/jobs`) e resultados (`GET /api/relatorios/resultados/{viagem}`)
  respondem a partir do banco; logs sem avisos recorrentes de "Banco indisponível".

### 8. Plano de rollback

Acione se a validação do cutover falhar, houver perda/corrupção de dados ou erros que
impeçam a operação. O rollback **não apaga arquivos de `dados/`** e **não remove linhas
fisicamente** (soft delete), exceto o restore do backup (destrutivo por natureza).

1. Parar `backend` e `worker` para cessar escritas.
2. **Desfazer a ETL** das viagens importadas:
   ```bash
   docker compose exec backend python -m src.migrar_dados_legados \
       --rollback /app/dados/migracao_relatorio.json
   ```
   Isso faz soft delete apenas nas viagens com `criada = true`; rodar de novo é
   inofensivo. Viagens que já existiam (`criada = false`) podem ter sido atualizadas
   pela ETL e **não** são revertidas por esse comando.
3. **Restaurar o backup anterior** para o estado exato de antes do cutover (cobre as
   viagens pré-existentes e qualquer outra alteração):
   ```bash
   ./scripts/db_restore.sh backups/rdt01_<utc>.dump
   ```
4. Se o problema for só o schema/migração e **não** houver dados a preservar,
   `alembic downgrade base` remove as tabelas; havendo dados, prefira o restore do backup.
5. Religar `backend` e `worker`; conferir `/health`, logs e o fallback legado em `dados/`.
6. Registrar no card: horário, arquivo de backup usado, relatório da ETL, motivo e
   evidências da validação.

**Critérios de rollback**: health/`database` falso após o cutover; contagens ou
checksum divergentes sem explicação; leitura de dados errada/ausente; duplicação;
schema inaplicável. O restore do backup é o rollback autoritativo.

### Variáveis de ambiente e segredos

Modelo em `.env.example` (valores de exemplo); o `.env` real é ignorado pelo git.

| Variável | Onde | Obrigatória | Segredo | Descrição |
|---|---|---|---|---|
| `POSTGRES_DB` | compose | não (padrão `rdt01`) | não | Nome do banco |
| `POSTGRES_USER` | compose | não (padrão `rdt01`) | não | Usuário do banco |
| `POSTGRES_PASSWORD` | compose | **sim** | **sim** | Senha; sem ela o compose falha de propósito |
| `POSTGRES_PORT` | compose | não (padrão `5432`) | não | Porta publicada no host |
| `DATABASE_URL` | backend/worker | no compose é montada; fora dele, sim | **sim** (contém senha) | `postgresql://user:senha@host:5432/db` |
| `REDIS_URL` | backend/worker | não (padrão `redis://redis:6379/0`) | depende | URL da fila RQ |
| `MODELOS_DIR` | backend/worker | não (`/app/modelos`) | não | Diretório dos modelos |
| `DADOS_DIR` | backend/worker | não (`/app/dados`) | não | Volume dos artefatos legados |
| `SOURCES_CONFIG` | backend | não (`/app/config/sources.json`) | não | Configuração de fontes |
| `DEBUG` | backend | não (`false`) | não | Modo de depuração |
| `JOB_RETENTION_DAYS` | backend/worker | não (padrão 30) | não | Retenção de jobs antes do soft delete |
| `MODEL_IDLE_TTL_SECONDS` | backend/worker | não (padrão 300) | não | Segundos ociosos antes de descarregar modelos e liberar VRAM; `0` desabilita |
| `CORS_ORIGINS` | backend | não (`["*"]`) | não | Origens permitidas |
| `NVIDIA_VISIBLE_DEVICES` | backend/worker | não | não | GPU exposta aos containers |
| `NVIDIA_DRIVER_CAPABILITIES` | backend/worker | não | não | Capacidades do driver NVIDIA |
| `DATABASE_TEST_URL` | testes | não | **sim** | Banco efêmero da suíte; nunca apontar para produção |

Regras de segredo: nunca commitar `.env`/`DATABASE_URL`/backups; usar apenas valores de
exemplo em `.env.example` e na documentação; não imprimir `DATABASE_URL` em logs ou no
relatório da ETL (o relatório traz checksums e contagens, não credenciais). Para trocar a
senha, altere-a no banco (`ALTER ROLE ... PASSWORD ...`), atualize o `.env` e reinicie
`backend`/`worker` — só `POSTGRES_*` no compose não altera a senha de um volume já
inicializado.

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
  autoritativo `backend/migrations/0001_schema_inicial.sql`. A migração
  `0002_resultado_amostra.py` acrescenta `resultado_analise.amostra` e a chave única
  `(viagem, km, tipo_analise, amostra)` para o ICP por amostra.
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

O histórico de jobs e os resultados IGG/ICP ficam em `job` e `resultado_analise`
(RDT1-13), descritos abaixo. A ETL das viagens legadas em `dados/` está em
[ETL dos dados legados (RDT1-14)](#etl-dos-dados-legados-rdt1-14).

### Persistência de análise/detecções/parâmetros (RDT1-12)

O segundo slice vertical (`backend/src/services/analise_store.py`) coloca a análise no banco:

* A inferência grava as detecções de **cada faixa em uma transação curta** (substituição
  completa da faixa), sem escrita parcial de JSON. Se o banco/faixa não estiver
  disponível, o job apenas registra aviso e segue; o `analise_completa.json` continua
  sendo escrito **uma única vez ao fim do job** para exportação/compatibilidade.
* Detecções usam `deteccao` (classe, confiança, área, `global_box`/`global_polygon` em
  `JSONB`, linha/coluna). Os campos do JSON que não têm coluna (`area`, `score`) são
  derivados na leitura, e `direction: "manual"` mapeia `origem = 'manual'`.
* Parâmetros TRI/TRE ficam em `parametro_km` por `(viagem, km)`: `TRI (mm)`/`TRE (mm)`
  vão para as séries e o restante do objeto do editor para `extras`. Reprocessar
  reconcilia com soft delete os KMs que saíram do conjunto.
* `GET /api/pastas/analise/{viagem}` lê detecções e parâmetros do banco e cai no JSON
  legado quando a viagem ainda não está no banco; `POST .../salvar` grava no banco em
  uma transação e mantém a escrita do JSON durante a transição.
* Análises antigas podem ser importadas para o banco com
  `analise_store.migrar_analise_legada(...)`, que aceita o formato antigo (lista) e o
  novo (dict com `deteccoes`/`faixa_meta`). Faixas ausentes no banco são ignoradas — a
  concatenação (RDT1-11) precisa ter rodado antes.

### Histórico de jobs e resultados IGG/ICP (RDT1-13)

O terceiro slice vertical (`backend/src/services/job_store.py` e
`backend/src/services/resultado_store.py`) tira do Redis o histórico e os
resultados:

* Ao enfileirar, a API grava um registro em `job` ligado ao `rq_job_id`
  (`tipo` `concatenacao`, `inferencia` ou `upload_zip`, `meta` e status
  `queued`). O worker atualiza o ciclo de vida (`started` -> `finished`/`failed`),
  o progresso por lote e o resultado/erro. O Redis/RQ continua sendo só a fila.
* `GET /api/pastas/status/{job_id}` e `GET /api/pastas/jobs` leem do banco e caem
  no Redis quando o banco não está disponível ou ainda não há registros (jobs
  antigos). `DELETE /api/pastas/jobs/{job_id}` também remove pelo banco, então o
  histórico sobrevive a um flush do Redis.
* Retenção: jobs finalizados criados antes de `JOB_RETENTION_DAYS` (padrão 30
  dias) recebem soft delete (`job.deleted_at`) ao fim de cada job
  (`job_store.limpar_jobs_antigos`).
* Os relatórios persistem os resultados por `(viagem, km, tipo_analise)` em
  `resultado_analise` (`POST /api/relatorios/igg/{viagem}`). Recalcular o mesmo KM
  atualiza o registro; recalcular o conjunto completo reconcilia com soft delete
  os KMs que saíram. `GET /api/relatorios/resultados/{viagem}?tipo=igg|icp` lê os
  resultados persistidos.
* O `detalhe` (`JSONB`) guarda o restante do resultado (grupos, parcelas). O
  cálculo do ICP de rígido existe em `icp_calculator.py` (método da
  concessionária, ver [Cálculo do ICP](#cálculo-do-icp-pavimento-rígido--método-da-concessionária));
  e a importação da ficha (`icp_import.py`) já persiste `tipo_analise = 'icp'`
  por amostra (`POST /api/relatorios/icp/{viagem}`); falta apenas ligar a geração
  ao job automático de inferência.
* Sem banco configurado/indisponível, todas as operações de job/resultado são
  *best-effort* (apenas logam aviso); a API e o worker continuam funcionando com o
  fallback legado.

### ETL dos dados legados (RDT1-14)

`backend/src/services/migracao_legado.py` (CLI em `backend/src/migrar_dados_legados.py`)
migra as viagens que ainda vivem só no volume `dados/` para o banco. Lê, por pasta de
viagem, `viagem_config.json`, `lote_*_meta.json`, `analise_completa.json` e
`parametros_adicionais.json`, e grava em uma transação por viagem, nesta ordem:

1. projeto padrão `RDT01`, viagem/config, blocos e faixas
   (`viagem_store.persistir_viagem_em_sessao`);
2. detecções e parâmetros TRI/TRE por KM (`analise_store.persistir_analise_em_sessao`).

A mesma transação mantém o advisory lock da viagem, então **dois jobs/processos de
migração concorrentes não duplicam** linhas. O `viagem_config.json` no vocabulário
legado (`tipo_faixa`, `duplicada`) é normalizado para `tipo_pista`/`dupla`.

Uso (dentro do container, com `DATABASE_URL` definido):

```bash
# Simulação: lê os arquivos, imprime contagens/checksums e NÃO grava no banco.
python -m src.migrar_dados_legados --dry-run

# Execução real: grava e gera o relatório/manifesto de rollback.
python -m src.migrar_dados_legados --report migracao_relatorio.json

# Rollback a partir do relatório.
python -m src.migrar_dados_legados --rollback migracao_relatorio.json
```

O relatório JSON traz, por viagem, contagens (`blocos`, `faixas`, `analise_faixas`,
`deteccoes`, `parametros`), o SHA-256 de cada arquivo de origem (`arquivos`), um
`origem_checksum`, um `dados_checksum` do dataset normalizado, o `viagem_id` e se a
viagem foi `criada` pela ETL. O `checksum_consolidado` fecha o conjunto. Rodar a ETL
duas vezes produz o mesmo `checksum_consolidado` e as mesmas contagens (idempotência
por chaves naturais: `projeto`/`nome`, `bloco.indice`, `faixa.arquivo`, `(viagem, km)`).

**Rollback.** O script `--rollback` faz soft delete (detecções, parâmetros,
resultados, faixas, blocos e a viagem) apenas das viagens com `criada = true`, isto é,
que não existiam antes da migração; rodar o rollback de novo é inofensivo. Para
viagens que já existiam (`criada = false`) a ETL pode ter atualizado linhas
pré-existentes, então o rollback autoritativo é restaurar o **backup anterior**
(`scripts/db_backup.sh` antes da ETL e `scripts/db_restore.sh` para voltar). Nenhuma
viagem é removida fisicamente: todo o rollback usa `deleted_at` (soft delete).

Testes em `backend/tests/test_migracao_legado.py` (fixtures em disco, dry-run,
idempotência, concorrência e rollback), rodando com `DATABASE_TEST_URL`/`pgserver`
como os demais testes de banco.

### Placas do pavimento rígido e amostras de ICP (RDT1-18)

A etapa de placas (`backend/src/services/icp_placas.py`) usa o pacote
`juntas-detector@v0.2.0` para materializar a grade de placas do rígido. Ela não
depende de GPU: a geometria roda em CPU. Há duas origens de geometria:

* **Juntas detectadas na imagem** (preferido, RDT1-32) — a segmentação do modelo
  (`juntas-detector` + `trinca-detector`) devolve a posição (`km`) e a inclinação
  de cada transversal; `gerar_placas_por_juntas` monta cada placa como o
  intervalo entre **duas juntas consecutivas reais**. O comprimento de cada
  placa vem da imagem e **pode variar dentro da mesma viagem** — não se declara
  um tamanho fixo de placa. A largura vem da extensão da junta na faixa.
* **Rede periódica** (fallback) — `periodo_m` + `fase_m`, ou `juntas_km`
  calibradas, materializam `placas.posicoes_rede(fase + k·P)`.

* **Placas** — no modo por juntas cada placa é o quadrilátero entre duas
  transversais e a extensão lateral da junta; no modo periódico,
  `placas.poligonos` fecha cada placa entre duas transversais e duas
  longitudinais. `placas.distancia_transversais` mede o comprimento em metros.
  Placas > `comprimento_max_m` (padrão 9 m), em lacuna de amostragem ou sem
  sobreposição lateral entre as juntas são descartadas.
* **Coordenadas** — cada canto volta para a faixa original de 5 m
  (`pontos_faixa`: arquivo, `x`, `y`) e também é exposto em `poligono_m`
  (`x_m`, `km`). O topo da faixa é o menor KM.
* **Amostras** — placas ordenadas por KM são agrupadas de 20 em 20; cada placa
  recebe `amostra_id`.
* **Persistência** — inventário em `dados/<viagem>/icp_placas.json` e, quando
  os PNGs das faixas existem, ROI por placa em `dados/<viagem>/icp_rois/`.

API: `POST /api/relatorios/icp/{viagem}/placas` gera/persiste, aceitando
`detectar: true` (roda o modelo e usa a geometria segmentada), `juntas`
(geometrias já detectadas) ou, como fallback, `periodo_m`/`fase_m`/`juntas_km`;
`GET .../icp/{viagem}/placas` e `GET .../icp/{viagem}/amostras` leem.

**Detalhe e recorte da placa (RDT1-24).**
`GET /api/relatorios/icp/{viagem}/placas/{placa_id}` devolve a placa, a amostra
a que pertence, os ids da placa anterior/próxima **dentro da amostra** e se há
recorte. `GET .../placas/{placa_id}/recorte` serve o PNG gravado em
`icp_rois/`; o nome do arquivo vem do inventário (`placa.roi.arquivo`) e é
validado contra o diretório da viagem (sem path traversal, 400 quando inválido,
404 quando a placa/arquivo não existe). A tela do ICP usa esses endpoints no
painel lateral: clicar numa placa abre o recorte ampliado com KM, amostra,
comprimento/largura e botões anterior/próxima; placa sem ROI mostra aviso.

### Blocos do ICP por transversais × longitudinais (RDT1-33)

O caminho preferido do job do rígido (quando não há calibração manual) roda
`icp_juntas.detectar_estrutura` e `icp_blocos.montar_blocos`:

1. **Concatenação** — as faixas de 5 m de cada bloco (≈20 m) são empilhadas em
   uma única imagem cinza; o modelo roda **uma vez por bloco**.
2. **Duas famílias de juntas** — as transversais saem de
   `geometria.agrupar` (orientação `"h"`, posição em `km` absoluto) e as
   longitudinais de `longitudinais.fragmentos_verticais` →
   `agrupa_por_retas` → `valida`, com o par separado por 3,0–4,5 m
   (`x = a·y + b`, convertido para o `y` global pela origem do bloco).
3. **Cruzamento** — `placas.poligonos` fecha cada bloco entre duas transversais
   consecutivas e as duas longitudinais. Cada bloco tem id (`P0001`…),
   contorno/máscara, `origem_juntas` e `origem_largura`
   (`detectada`/`virtual`).
4. **Juntas faltantes (virtuais)** — mede-se o tamanho médio dos blocos já
   fechados; para o vão entre duas juntas detectadas, `k = round(vão / médio)`.
   Se `vão ≈ k × médio` (tolerância de 35 % do médio), o vão é dividido em `k`
   partes iguais e as `k−1` juntas internas são marcadas `virtual`. Se não for
   múltiplo coerente, **não força**: o trecho vai para `blocos_sem_mascara` e
   gera aviso. A mesma ideia vale para a largura: sem par no bloco, reaproveita
   a largura média/par mais próximo e marca `origem_largura = "virtual"`.
5. **Inventário** — `montar_inventario(..., estrutura=...)` grava as placas
   fechadas em `icp_placas.json` com o polígono no strip, `pontos_faixa` e
   `poligono_m`, além de `blocos_sem_mascara` e `avisos`. Blocos sem máscara não
   entram em `placas` (ficam registrados e sinalizados na tela).

Na **tela do ICP** o overlay desenha os blocos sobre a faixa concatenada
(rotacionada 90°, como no IGG), com id e contorno; blocos fechados por linha
virtual aparecem com contorno **tracejado** e sufixo `~`. Os botões
**◀ Anterior / Próxima ▶** navegam para frente e para trás entre as imagens da
viagem. Avisos e a contagem de blocos sem máscara aparecem no resumo do
inventário.

API: `POST .../placas` com `detectar: true` usa esse caminho; a resposta e o
`GET .../placas` trazem `blocos_sem_mascara` e `avisos`. Regressão em
`backend/tests/test_icp_blocos.py` (vão múltiplo → virtuais; vão incoerente →
sem máscara; cruzamento com longitudinais; integração no inventário).

**Sem alteração no IGG** — o caminho do IGG não usa `icp_blocos` nem as novas
chaves de inventário.

### Marcação manual de patologias no ICP (RDT1-23)

Além da ficha importada, o operador pode marcar defeitos do Anexo A **por
placa** no painel da tela do ICP (`icp_marcacoes.py`):

* a marcação guarda placa, amostra, KM, faixa, tipo (1–18), severidade (B/M/A),
  geometria opcional, autor/data e `ativo` (remoção é *soft delete*);
* a densidade na amostra é `placas_afetadas / total_placas`, contando **placas
  distintas**; a mesma placa não pode repetir o mesmo tipo/severidade (409);
* o cálculo é sempre refeito a partir de `defeitos_ficha` **somado** às
  marcações ativas, então reimportar a ficha não apaga nem duplica a edição
  manual; cada defeito no resultado expõe `placas_afetadas_ficha` e
  `placas_afetadas_manual`, e `composicao` resume as duas origens;
* as marcações vivem em `dados/<viagem>/icp_marcacoes.json` (fonte para a
  edição, funciona sem banco) e são espelhadas no `detalhe` do resultado
  persistido; o recálculo usa `calcular_icp_amostra` e é persistido
  best-effort em `resultado_analise`.

API: `GET/POST /api/relatorios/icp/{viagem}/marcacoes`,
`PATCH/DELETE .../marcacoes/{id}`. Validações: tipo/severidade inválidos (400),
placa/amostra sem placas (400/404), duplicidade (409).

Decisões (registradas no card): marcação por placa individual agregada na
amostra; edição manual **adicional** à ficha; autor/data em cada marcação sem
fluxo de aprovação. Regressão em `backend/tests/test_icp_marcacoes.py`.

Validação BR-364 (rede periódica): 120 m com período de **5,12 m** gera 23
placas; com **4,10 m** gera 29 placas (`backend/tests/test_icp_placas.py`). A
validação por juntas cobre vãos variáveis na mesma viagem (5,12 / 5,10 / 4,78 /
5,50 m), confirmando que o tamanho não é fixo (`gerar_placas_por_juntas`).

### Processamento e juntas no job do rígido (RDT1-21 / RDT1-22)

O cartão **ICP** inicia o mesmo fluxo das imagens (configuração → upload →
processamento), mas passa `tipo_analise = "icp"` para a API. O job então:

1. concatena **todas as faixas de 5 m em contínuo** (sem pular blocos e sem a
   amostragem 25/50 do IGG), porque as amostras do ICP são grupos de 20 placas;
2. calibra as juntas do trecho e materializa o inventário de placas, as amostras
   e as ROIs (`icp_placas.json`, `icp_juntas.json`, `icp_rois/`).

A calibração (`backend/src/services/icp_juntas.py`) aceita, nesta ordem:

* `juntas_km` (lista de KM absolutos) — override manual/depuração;
* `periodo_m` + `fase_m` — calibração explícita;
* **detecção automática**: pacote `juntas-detector` v0.2.0 (wheel) +
  modelo externo `trinca-detector` v2.1.0 (GPL-3.0), via `ProvedorTrincaDetector`.
  O detector devolve período/fase **e** a geometria de cada junta segmentada
  (`km`, `angulo_graus`, `x0`, `x1`); a etapa de placas usa essa geometria para
  montar cada placa entre duas juntas reais (tamanho variável na viagem).

Configuração em `modelos/icp/config.json`, bloco `"juntas"`:

```json
{
  "juntas": {
    "nuclio_dir": "/opt/trinca-detector/nuclio",
    "modelo_pt": "/opt/trinca-detector/nuclio/modelos/trinca_junta_limpo_ts.pt",
    "limiar": 0.8,
    "mm_por_px_y": 1.953125
  }
}
```

`Settings` também aceita `ICP_NUCLIO_DIR`, `ICP_MODELO_PT`, `ICP_LIMIAR_JUNTAS`,
`ICP_ORDEM_INVERTIDA` e `ICP_MM_POR_PX_Y`. A configuração/blocos/faixas são
persistidos no banco **antes** da etapa de placas. Se faltar modelo, calibração
ou imagens, ou se a detecção falhar por qualquer erro inesperado (por exemplo
uma dependência ausente no deploy, como `skimage`), o job do rígido conclui com
**aviso explícito** (`placas_geradas = false` e `avisos` no resultado, exibido em
**Processamentos**) em vez de "sucesso silencioso" ou de falhar o job inteiro; a
concatenação já produzida não é perdida e o `logger.warning` registra a causa.

O resultado do job guarda a calibração e um resumo estruturado do inventário
(`inventario_resumo`: trecho e amostras) no banco; o inventário completo
continua em `icp_placas.json` para consulta/transição.

Os pesos/modelo vêm do release público
`https://github.com/victormedeiros96/trinca-detector-public/releases/tag/0.1`
(`trinca_junta_limpo_ts.pt`, sha256 `901b70ad…c3e6`).

* **Coolify**: o `backend/Dockerfile.coolify` tem um estágio `modelo-icp` que
  baixa e confere o código + pesos e os copia para `/opt/trinca-detector` na
  própria imagem do backend/worker. Não há volume a popular — basta rebuild.
  As `ARG` `TRINCA_RELEASE`/`TRINCA_SHA256` permitem trocar a versão.
* **Local/compose**: `scripts/baixar_modelo_trinca.sh` baixa para
  `ICP_MODEL_DIR` (padrão `/opt/trinca-detector`) e o compose monta o diretório
  em `/opt/trinca-detector`.

#### GPU/CUDA no deploy (RDT1-30)

A detecção automática de juntas exige **GPU**: em CPU ela fica muito lenta e,
hoje, o container não deve cair em CPU silenciosamente. No Coolify
(`docker-compose.coolify.yml`) o `backend` e o `worker` declaram a GPU de duas
formas complementares:

```yaml
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    runtime: nvidia
```

O Compose v2 ignora o campo legado `runtime: nvidia` sem a reserva explícita de
dispositivo.

O `torch`/`torchvision` são fixados no **CUDA 12.6**
(`--extra-index-url https://download.pytorch.org/whl/cu126`,
`torch==2.14.1+cu126`, `torchvision==0.29.1+cu126`) para casar com a base
`nvidia/cuda:12.6.3` e com o driver do host (550.163.01 → CUDA 12.6). Sem o
pino, o `torch>=2.4.0` resolvia para wheels CUDA 13, que esse driver não
suporta: `torch.cuda.is_available()` virava `false` e a inferência caía em CPU.

Para conferir o ambiente (endpoint público de diagnóstico):

```bash
curl -s https://0650163.xyz/debug/gpu
# esperado:
# {"torch":"2.14.1+cu126","cuda_build":"12.6","cuda_disponivel":true,
#  "device_count":2,"devices":["NVIDIA RTX A5500","NVIDIA RTX A5500"], ...}
```

Após uma inferência (`POST /debug/inferir-imagem-pt`), `GET /debug/modelos` deve
trazer `vram` com valores > 0 (ex.: `allocated_mb`/`reserved_mb`); `vram: null`
significa CUDA não inicializada (container sem GPU ou torch/driver
incompatíveis). No host, `nvidia-smi` e `docker info | grep -i runtime` (deve
listar `nvidia`) confirmam driver e runtime.

O `trinca-detector` é GPL-3.0 e permanece isolado em `/opt/trinca-detector`,
acessado por adaptador (`ProvedorTrincaDetector`).

O fluxo do processamento segue com o job visível em **Processamentos**. Ao
concluir, o botão **Abrir ICP** leva à tela de ficha/resultado. A calibração
manual continua disponível na própria tela do ICP (`POST .../placas`).

### Tela e relatório do ICP na UI (RDT1-19)

O cartão **ICP** fica habilitado na tela inicial (a entrada é a ficha de
inspeção, não um modelo de imagem) e abre a tela `IcpView` (aba **ICP** no menu).
O fluxo completo é: informar a **viagem** → importar a ficha (`.xlsx`, `.xlsm`
ou `.csv`) → conferir o resultado → gerar/conferir o overlay de placas →
exportar o laudo.

* **Resultado por amostra** — tabela com amostra, rodovia/KM, faixa, nº de
  placas, VDT, `q`, VDC, ICP e conceito. Cada linha abre o detalhe dos defeitos
  (tipo, nome, severidade, densidade, placas afetadas e VD).
* **Resumo** — total de amostras, ICP médio, melhor/pior e distribuição por
  conceito.
* **Inventário de placas** — a própria tela gera as placas
  (`POST /api/relatorios/icp/{viagem}/placas`) a partir de período/fase,
  largura, comprimento máximo e placas por amostra, e lê o inventário
  persistido (`GET .../placas`).
* **Overlay** — a faixa original é exibida rotacionada 90° com a mesma
  convenção do IGG; as ROIs de placa são desenhadas a partir de
  `km_inicio/km_fim` e das metadados das faixas (`GET /api/pastas/faixas/{viagem}`),
  e as patologias detectadas (quando existe análise) aparecem tracejadas.
* **Detalhe da placa** — clicar na placa (no overlay ou na lista lateral) abre o
  recorte real da imagem em `icp_rois/`, com KM início/fim, amostra,
  comprimento/largura e navegação anterior/próxima dentro da amostra
  (`GET .../placas/{id}` e `GET .../placas/{id}/recorte`, RDT1-24). Placa sem
  recorte exibe aviso em vez da imagem.
* **Editor manual** — no detalhe da placa, escolher tipo (1–18) e severidade
  (B/M/A), marcar/remover patologias e ver o ICP da amostra recalculado na hora
  (`/marcacoes`, RDT1-23). O resumo mostra a composição (defeitos da ficha ×
  marcações manuais).
* **Exportar laudo** — `GET /api/relatorios/icp/{viagem}/exportar` devolve um
  CSV com uma linha por defeito (amostra, metadados, tipo/defeito, severidade,
  densidade, placas afetadas, VD) e as colunas de resumo (VDT, q, VDC, ICP,
  conceito).
* **Erros** — a mensagem do backend é exibida sem alteração (ficha inválida,
  formato não suportado, viagem inexistente, banco indisponível). Sem inventário
  ou com zero placas, a tela avisa para revisar a calibração e sinaliza amostras
  sem placas.

Regressão: `backend/tests/test_api_icp.py` cobre também a exportação (CSV com
defeitos e 404 sem resultados).


### Backup e restore

```bash
./scripts/db_backup.sh                       # grava backups/rdt01_<utc>.dump (pg_dump -Fc)
docker compose stop backend worker
./scripts/db_restore.sh backups/<arquivo>.dump
docker compose start backend worker
```

Os scripts rodam `pg_dump`/`pg_restore` via `docker compose exec`, sem exigir cliente PostgreSQL no
host. Restaure com backend/worker parados para não haver conexões abertas durante o `--clean`.
`backups/` está no `.gitignore`. Uso dentro do cutover/rollback em
[Operação — Guia de Execução](#operação--guia-de-execução).

### Pendências

* O schema é aplicado por `alembic upgrade head` (manual/de deploy); o container ainda não roda a
  migração automaticamente na subida.
* O ICP de **rígido** está implementado como método da concessionária
  (`icp_calculator.py`, 108 casos de regressão) e o job do rígido (RDT1-21)
  gera faixas contínuas, juntas e placas (RDT1-22). A ficha de inspeção continua
  sendo importada separadamente (`icp_import.py` / `POST /api/relatorios/icp/{viagem}`)
  e a UI (`IcpView`, RDT1-19) cobre importar → resultado → overlay → exportar.
  A edição manual das patologias por placa está em RDT1-23 (`icp_marcacoes.py`
  + painel na `IcpView`). Ainda falta: laudo completo (#26) e definir o ICP do **flexível**, que segue em
  aberto. O modelo de **patologias** do rígido (além das juntas) também fica
  para depois. A ETL dos jobs/resultados
  legados (que vivem no Redis, não em `dados/`) continua pendente; a ETL por
  arquivo (RDT1-14) cobre viagem/config/blocos/faixas/análise/parâmetros.
* O cutover e o rollback descritos acima precisam ser ensaiados/revisados em ambiente com
  Docker e PostgreSQL antes de serem considerados aprovados; este workspace não tem Docker,
  `psql` nem as dependências Python, então só os comandos e o dry-run da ETL são verificáveis
  aqui (os testes de banco rodam em `DATABASE_TEST_URL`/`pgserver`).