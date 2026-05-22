# Relatório Técnico — Agendador de Backup de Notebooks
**Projeto:** Ultracognia Notebook Green  
**Módulo:** `app/scheduler`  
**Data:** 22/05/2026

---

## Visão Geral

O agendador realiza o **backup diário automático dos notebooks do NotebookLM**. Todos os dias, em um horário configurável, o sistema percorre todos os usuários que tiveram conversas naquele dia, e para cada um cria (ou reutiliza) um notebook no NotebookLM com o histórico de mensagens — sem a necessidade de intervenção humana.

O objetivo é garantir que o conteúdo das conversas esteja sempre disponível e indexado, pronto para ser usado na geração de relatórios posteriores.

---

## Arquitetura do Módulo

```
app/
├── main.py                         ← Startup: inicializa o scheduler no lifespan
├── scheduler/
│   ├── scheduler.py                ← Configura o APScheduler (cron)
│   └── backup_job.py               ← Lógica do job em si
├── services/
│   └── report_service.py           ← Orquestração compartilhada (HTTP + scheduler)
├── repositories/
│   ├── users.py                    ← Busca usuários com mensagens no dia
│   ├── conversations.py            ← Busca mensagens por usuário e data
│   └── notebooks.py                ← Persistência dos notebooks no banco
└── core/
    ├── database.py                 ← Pool de conexões PostgreSQL
    └── settings.py                 ← Configuração via variáveis de ambiente
```

---

## Componentes e Responsabilidades

### `main.py` — Inicialização

O FastAPI utiliza o padrão `lifespan` para gerenciar a vida do scheduler. Isso garante que o scheduler seja iniciado junto com a API e encerrado de forma limpa quando o container for desligado.

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_pool()       # Inicia pool de conexões PostgreSQL
    scheduler = create_scheduler()
    scheduler.start()         # Inicia o APScheduler
    yield
    scheduler.shutdown()      # Encerra o scheduler ao desligar
    await close_pool()
```

> **Evidência nos logs:**
> ```
> Added job "backup_notebooks_daily" to job store "default"
> Scheduler started
> Application startup complete.
> ```

---

### `scheduler/scheduler.py` — Configuração do Cron

Usa a biblioteca **APScheduler** no modo `AsyncIOScheduler` — compatível com o event loop do FastAPI/Uvicorn, sem necessidade de thread separada ou broker externo (Redis, RabbitMQ etc.).

| Parâmetro | Padrão | Variável de Ambiente |
|---|---|---|
| Fuso horário | `America/Sao_Paulo` | — |
| Hora de execução | `23` | `BACKUP_SCHEDULE_HOUR` |
| Minuto de execução | `0` | `BACKUP_SCHEDULE_MINUTE` |

A opção `replace_existing=True` evita duplicação de jobs ao reiniciar a aplicação.

---

### `scheduler/backup_job.py` — O Job

É a função disparada pelo cron. Abre sua **própria conexão direta ao banco** (independente do pool do FastAPI, que não é acessível no contexto do scheduler) e executa o fluxo:

```
Para cada usuário com mensagens hoje:
    └── chama orchestrate_prepare_notebook()
         ├── [cache hit]  → retorna o notebook existente (pula criação)
         └── [cache miss] → cria notebook novo no NotebookLM + salva no banco
```

Falhas individuais são **capturadas por usuário** e logadas sem interromper o processamento dos demais. Ao final, o job loga um resumo:
```
Backup concluído — criados: X, pulados: Y
```

---

### `services/report_service.py` — Orquestração Centralizada

A função `orchestrate_prepare_notebook()` é o **núcleo compartilhado** entre o scheduler e o endpoint HTTP `POST /report/prepare-notebook`. Isso elimina duplicação de lógica.

#### Fluxo completo:

```
orchestrate_prepare_notebook(conn, user_id, target_date)
│
├── 1. Verifica cache no banco (tabela notebooks)
│       └── Se já existe → retorna from_cache=True (não chama a API)
│
├── 2. Busca nome do usuário (tabela users)
│
├── 3. Busca mensagens do dia (tabelas conversations + messages)
│       └── Filtra por user_id, data e status='ok'
│       └── Ordena cronologicamente
│
├── 4. Chama a API do NotebookLM (_call_notebooklm_prepare)
│       ├── Constrói título: "Nome_Sobrenome-YYYY-MM-DD"
│       ├── Cria o notebook
│       ├── Injeta prompt proprietário como fonte oculta [config]
│       ├── Adiciona o histórico de mensagens como fonte principal
│       └── Remove a fonte [config] (protege o prompt)
│
└── 5. Persiste notebook_id e notebook_title no banco
```

---

### `repositories/` — Camada de Dados

| Repository | Função |
|---|---|
| `UserRepository` | Retorna UUIDs de usuários com mensagens (`status='ok'`) em uma data |
| `ConversationMessageRepository` | Retorna mensagens ordenadas por `created_at` de um usuário em uma data |
| `NotebookRepository` | Lê e grava registros na tabela `notebooks` (cache + persistência) |

#### Schema da tabela `notebooks`:
| Coluna | Tipo | Descrição |
|---|---|---|
| `user_id` | UUID | Referência ao usuário |
| `notebook_id` | TEXT | ID do notebook no NotebookLM |
| `notebook_title` | TEXT | Título no formato `Nome-Data` |
| `target_date` | DATE | Data de referência |
| `report_content` | TEXT | Conteúdo do relatório (preenchido depois) |
| `report_path` | TEXT | Caminho do arquivo `.md` local |

A constraint `UNIQUE(user_id, target_date)` garante **no máximo um notebook por usuário por dia**, com upsert em caso de reprocessamento.

---

## Fluxo Completo em Produção

```
23:00 (America/Sao_Paulo)
│
▼
APScheduler dispara backup_notebooks_daily()
│
├── Conecta direto ao PostgreSQL (asyncpg.connect)
│
├── UserRepository → "Quais usuários tiveram mensagens hoje?"
│       └── SELECT DISTINCT user_id FROM messages JOIN conversations WHERE date = hoje
│
└── Para cada user_id:
        │
        ├── orchestrate_prepare_notebook()
        │       │
        │       ├── Cache hit? → loga "pulando", skipped++
        │       │
        │       └── Cache miss?
        │               ├── Busca mensagens do dia
        │               ├── Cria notebook no NotebookLM
        │               │     Título: "Nome_Sobrenome-2026-05-22"
        │               ├── Salva notebook_id no banco
        │               └── created++
        │
        └── Erro? → loga exception, continua para próximo usuário

Fim: "Backup concluído — criados: X, pulados: Y"
│
└── Fecha conexão
```

---

## Configuração via `.env`

```env
DATABASE_URL=postgresql://...        # Conexão com o PostgreSQL
BACKUP_SCHEDULE_HOUR=23              # Hora do backup (padrão: 23h)
BACKUP_SCHEDULE_MINUTE=0             # Minuto do backup (padrão: 00min)
```

---

## Decisões de Projeto

| Decisão | Justificativa |
|---|---|
| `AsyncIOScheduler` sem broker externo | Simplicidade de deploy — roda no mesmo processo da API, sem depender de Redis ou fila |
| Conexão direta no job (`asyncpg.connect`) | O pool do FastAPI não é acessível fora do contexto de requisição HTTP |
| `orchestrate_prepare_notebook()` compartilhada | Evita duplicação: HTTP e scheduler usam a mesma lógica de orquestração |
| Cache por `(user_id, target_date)` | Evita chamadas redundantes à API do NotebookLM (Quota do NotebookLM é baixa e o processamento é lento) |
| Falha por usuário não interrompe o job | Resiliência: um erro em um usuário não bloqueia o backup dos demais |
| `replace_existing=True` no scheduler | Reinícios do container não criam jobs duplicados |

---

## Evidência de Funcionamento (Logs Reais)

```log
[INFO] apscheduler.scheduler: Adding job tentatively -- it will be properly scheduled when the scheduler starts
[INFO] apscheduler.scheduler: Added job "backup_notebooks_daily" to job store "default"
[INFO] apscheduler.scheduler: Scheduler started
[INFO] Application startup complete.
[INFO] Uvicorn running on http://0.0.0.0:8004
```

O scheduler inicializa corretamente junto com o servidor e estará aguardando o horário configurado para disparar o job de backup.
