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
- **Docker** — 4 serviços: `backend`, `worker`, `frontend`, `redis`.

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

O sistema detecta e classifica as seguintes patologias:

| Código | Patologia |
|---|---|
| T / TB | Trincas / Trinca em bloco |
| J / JE | Couro de jacaré (com/sem erosão) |
| P | Panela |
| R | Remendo |
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