# ultracognia-notebook-green

API FastAPI (`brain_notebooklm`) que automatiza a geração de relatórios e slides no **NotebookLM** a partir do histórico de conversas armazenado em um PostgreSQL (banco compartilhado com o `ultracognia-frontend-green`).

Fluxo em uma frase: **conversas no Postgres → notebook criado no NotebookLM com esse histórico como fonte → relatório/slides gerados via IA**, com um agendamento semanal automático fazendo isso para todos os usuários.

---

## Sumário

- [Arquitetura](#arquitetura)
- [Requisitos](#requisitos)
- [Setup local](#setup-local)
- [Variáveis de ambiente](#variáveis-de-ambiente)
- [Rodando a API](#rodando-a-api)
- [Endpoints](#endpoints)
- [Agendamento (backup semanal)](#agendamento-backup-semanal)
- [Testes](#testes)
- [Docker](#docker)
- [Deploy (Railway)](#deploy-railway)
- [Scripts auxiliares](#scripts-auxiliares)

---

## Arquitetura

```
app/
├── routers/report.py        → Endpoints REST (/report/*)
├── services/report_service.py → Orquestração + integração com o NotebookLM
├── services/context_managers.py → Injeção do prompt de sistema oculto ([config])
├── repositories/             → Acesso a dados (asyncpg), um por domínio
├── scheduler/                → Job de backup semanal (APScheduler, in-process)
└── core/
    ├── database.py           → Pool de conexões PostgreSQL (com retry/backoff)
    └── settings.py           → Configuração via .env (pydantic-settings)
```

Camadas: **router → service → repository**. Toda a lógica de "buscar mensagens do usuário e criar um notebook" fica centralizada em `orchestrate_prepare_notebook()`, reaproveitada tanto pelo endpoint HTTP quanto pelo job agendado.

## Requisitos

- Python 3.12 (a imagem Docker usa `python:3.12-slim`)
- PostgreSQL acessível (schema com as tabelas `users`, `conversations`, `messages`, `notebooks`)
- Uma conta Google autenticada no NotebookLM (o projeto usa a lib não-oficial [`notebooklm-py`](https://pypi.org/project/notebooklm-py/), que automatiza o produto via sessão de navegador — não existe API key oficial do NotebookLM)

## Setup local

```powershell
# 1. Ambiente virtual
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\playwright install chromium

# 2. Configuração
copy .env.example .env
# preencha DATABASE_URL, API_KEY, SYSTEM_PROMPT, OUTPUT_DIR, SLIDE_DECK_INSTRUCTION

# 3. Autenticação no NotebookLM (uma vez, interativo)
.venv\Scripts\notebooklm login
# abre um navegador — loga com a conta Google que vai "possuir" os notebooks,
# espera a home do NotebookLM carregar, volta no terminal e aperta ENTER.
# A sessão fica salva em ~/.notebooklm/ e é usada automaticamente depois.
```

> **Não** defina `NOTEBOOKLM_AUTH_JSON` no `.env` local — essa variável tem prioridade sobre a sessão salva pelo `notebooklm login` e, se estiver com um valor de exemplo/inválido, quebra a autenticação. Ela existe para o cenário de produção (ver [Deploy](#deploy-railway)).

## Variáveis de ambiente

| Variável | Obrigatória | Descrição |
|---|---|---|
| `API_KEY` | sim | Chave exigida no header `x-api-key` em toda rota (exceto `/`, `/docs`, `/openapi.json`, `/health`) |
| `ENV` | não (padrão `production`) | `local` habilita CORS permissivo, usado pela interface de teste HTML (ver abaixo). Qualquer outro valor mantém CORS desligado |
| `DATABASE_URL` | sim | DSN do PostgreSQL. Conexão via SSL sem verificação de certificado (`CERT_NONE`) — compatível com Supabase |
| `SYSTEM_PROMPT` | sim | Prompt proprietário injetado como fonte oculta `[config]` antes de gerar relatório/slides |
| `OUTPUT_DIR` | sim | Pasta onde relatórios (`.md`) e slides (`.pdf`) baixados são salvos |
| `SLIDE_DECK_INSTRUCTION` | sim | Instrução fixa passada ao gerar o slide deck |
| `BACKUP_SCHEDULE_HOUR` | não (padrão `23`) | Hora do backup semanal, fuso `America/Sao_Paulo` |
| `BACKUP_SCHEDULE_MINUTE` | não (padrão `0`) | Minuto do backup semanal |
| `NOTEBOOKLM_AUTH_JSON` | não (local) / sim (produção) | Conteúdo JSON da sessão do NotebookLM, para rodar sem navegador (ver [Deploy](#deploy-railway)) |

## Rodando a API

```powershell
.venv\Scripts\uvicorn main:app --reload --port 8004
```

```bash
curl -s -X POST http://localhost:8004/report/prepare-notebook \
  -H "x-api-key: SUA_API_KEY" -H "Content-Type: application/json" \
  -d '{"user_id":"<uuid>","start_date":"2020-01-01","end_date":"2026-08-05"}'
```

## Endpoints

Todos sob `/report`, autenticados por `x-api-key`.

| Método | Rota | Descrição |
|---|---|---|
| `POST` | `/report/prepare-notebook` | Cria um notebook no NotebookLM com o histórico de conversas de um usuário |
| `POST` | `/report/generate` | Gera o relatório (markdown) de um notebook já preparado |
| `POST` | `/report/create-slides` | Gera o slide deck (PDF) de um notebook já preparado |
| `GET` | `/health` | Liveness check, sem autenticação |

Documentação interativa em `/docs` (Swagger).

## Agendamento (backup semanal)

Um `AsyncIOScheduler` roda dentro do próprio processo da API (iniciado no `lifespan` do `main.py`) e dispara `backup_notebooks_daily()` **toda sexta-feira**, no horário de `BACKUP_SCHEDULE_HOUR:BACKUP_SCHEDULE_MINUTE`. Para cada usuário com mensagens no banco, cria um notebook novo no NotebookLM com o histórico completo daquele usuário.

Não é necessário chamar nenhum endpoint para isso acontecer — basta o serviço estar de pé. Ver [`docs/scheduler.md`](docs/scheduler.md) para mais detalhes (nota: esse doc descreve uma versão anterior do schema/fluxo, mantido como referência histórica).

## Testes

```powershell
.venv\Scripts\pip install pytest pytest-asyncio pytest-cov
.venv\Scripts\python.exe -m pytest
```

> `pytest`/`pytest-asyncio`/`pytest-cov` não estão no `requirements.txt` (são dependências só de desenvolvimento) — instale à parte como acima.

## Docker

```bash
docker build -t ultracognia-notebook-green .
docker run -p 8004:8004 --env-file .env ultracognia-notebook-green
```

## Deploy (Railway)

O login interativo do `notebooklm login` não roda num container headless. O fluxo é:

1. **Localmente** (uma vez, numa máquina com navegador): rode `notebooklm login` com a conta Google de produção.
2. Copie o conteúdo do arquivo gerado (`~/.notebooklm/profiles/default/storage_state.json`).
3. No Railway, crie a variável de ambiente `NOTEBOOKLM_AUTH_JSON` com esse conteúdo colado como valor (secret).

No boot, `NotebookLMClient.from_storage()` detecta `NOTEBOOKLM_AUTH_JSON` e usa a sessão diretamente, sem precisar de navegador. Configure também as demais variáveis da tabela acima, incluindo `BACKUP_SCHEDULE_HOUR=23` / `BACKUP_SCHEDULE_MINUTE=0` para o backup de sexta-feira.

Essa credencial não é eterna — sessões Google podem expirar; se isso acontecer, repita os passos 1–3.

## Interface de teste local

[`tools/tester.html`](tools/tester.html) é uma página standalone (sem build, sem dependências) para testar os três endpoints de `report_service.py` pelo navegador, sem precisar de `curl`. Uso:

1. No `.env`, defina `ENV=local` (habilita CORS só nesse modo — nunca em produção).
2. Suba a API: `.venv\Scripts\uvicorn main:app --reload --port 8004`
3. Abra `tools/tester.html` direto no navegador (duplo clique).
4. Preencha a `x-api-key` (a mesma do `.env`) e teste preparar notebook, gerar relatório e criar slides — o `notebook_id` retornado é preenchido automaticamente nos passos seguintes.

Roda 100% local, sem telemetria — só chama a URL da API configurada na própria página.

## Scripts auxiliares

Fora da API, existe um pipeline separado para exportar conversas manualmente:

- `app/conversas/gerar_conversas.py` — gera `.txt`/`.json` por usuário.
- `gerar.py` / `gerar.spec` — entrypoint empacotável via PyInstaller (`gerar.exe`) para rodar esse export fora do container, como ferramenta desktop.
- `scripts/migrate_notebooks.py` — migração pontual já aplicada ao schema da tabela `notebooks` (mantida para histórico).
