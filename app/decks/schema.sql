-- Schema do módulo de geração de decks por IA.
-- Aplicado manualmente (não existe ferramenta de migração no projeto ainda —
-- ver pendência #12 no CLAUDE.md). Mantido aqui como referência de portabilidade:
-- na extração pro projeto final, isso vira a base de uma migração de verdade.

CREATE TABLE IF NOT EXISTS deck_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES users(id),
    client_id uuid REFERENCES clients(id),  -- dono da marca/tema; nullable até isso entrar em uso
    editorial_text text NOT NULL,  -- colado pelo usuário no chat da tela de geração; dado de entrada, não saída de etapa
    llm_provider varchar,  -- gemini | deepseek | openai (openrouter removido do combo em 2026-08-21); NULL = fallback automático de sempre
    apply_style_guardrails boolean NOT NULL DEFAULT false,  -- checkbox da tela -- liga/desliga o rulebook visual interno (ver structure.py). Default false (invertido em 2026-08-21 (4): padrão é extrair do editorial, sem opinião de estilo nossa; usuário liga explicitamente se quiser o rulebook)
    status varchar NOT NULL DEFAULT 'pending',  -- pending | running | completed | failed
    cost_cents integer NOT NULL DEFAULT 0,
    failure_reason text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Pipeline revisado (2026-08-18): a entrada já vem como editorial em texto
-- livre (colado pelo usuário), não mais como histórico de conversa cru — por
-- isso não existem mais etapas 'context' nem 'narrative'. 'structure' agora
-- absorve a interpretação do texto inteiro (separar em slides, escolher
-- layout via few-shot de imagens de referência, montar os blocos de
-- conteúdo) numa chamada só.
CREATE TABLE IF NOT EXISTS deck_job_steps (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id uuid NOT NULL REFERENCES deck_jobs(id) ON DELETE CASCADE,
    step varchar NOT NULL,  -- structure | assets | render | qa
    status varchar NOT NULL DEFAULT 'pending',  -- pending | running | completed | failed
    output_ref text,  -- JSON, formato varia por step; 'render' guarda {"pdf": "...", "pptx": "..."} (2026-08-19: dois formatos de saída, escolha na hora do download)
    input_tokens integer NOT NULL DEFAULT 0,
    output_tokens integer NOT NULL DEFAULT 0,
    cost_cents integer NOT NULL DEFAULT 0,
    attempt integer NOT NULL DEFAULT 0,
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (job_id, step)
);

CREATE INDEX IF NOT EXISTS idx_deck_job_steps_pending
    ON deck_job_steps (created_at)
    WHERE status = 'pending';

-- Migração manual 2026-08-20 (tabela já existia sem essa coluna — o
-- CREATE TABLE IF NOT EXISTS acima não altera tabela já criada):
-- ALTER TABLE deck_jobs ADD COLUMN IF NOT EXISTS llm_provider varchar;

-- Migração manual 2026-08-21 (tarefa 1 — checkbox de guardrail na tela):
-- ALTER TABLE deck_jobs ADD COLUMN IF NOT EXISTS apply_style_guardrails boolean NOT NULL DEFAULT true;

-- Migração manual 2026-08-21 (4) — inverte o default pra false (decisão do
-- usuário: guardrail visual é opt-in, não opt-out; achado real que motivou
-- isso, ver histórico: com o default antigo (true) e o campo não chegando
-- corretamente do frontend, editoriais com paleta clara própria estavam
-- caindo na validação de fundo escuro sem o usuário ter pedido isso):
-- ALTER TABLE deck_jobs ALTER COLUMN apply_style_guardrails SET DEFAULT false;

-- Migração manual 2026-08-21 (tarefa 2 — combo de 3 providers travados
-- ponta a ponta, OpenAI novo, OpenRouter removido do combo):
-- INSERT INTO providers (name, url, priority, is_active)
-- VALUES ('openai', 'https://api.openai.com/v1/chat/completions', 4, true);

-- Theme Library (2026-08-24, Fatia A — resposta ao pedido de arquitetura do
-- Ramon/TYPOGRAPHOS-Σ: separar "arquétipo" de "identidade de marca"). Tema
-- nomeado e reaproveitável por client_id — pra um job não depender do LLM
-- reinventar a paleta a cada geração. `palette` guardado como `text` (JSON
-- serializado), mesmo padrão de `deck_job_steps.output_ref` — não existe
-- codec jsonb configurado no asyncpg deste projeto (ver app/core/database.py),
-- então texto cru é mais simples e consistente do que introduzir tipagem
-- jsonb pela metade só pra esta tabela.
CREATE TABLE IF NOT EXISTS deck_themes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid REFERENCES clients(id),  -- nullable: tema pode não estar ligado a um cliente específico
    name text NOT NULL,
    palette text NOT NULL,
    font_stack text NOT NULL,
    logo_url text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (client_id, name)
);

-- Migração manual 2026-08-24 (Fatia A):
-- ALTER TABLE deck_jobs ADD COLUMN IF NOT EXISTS theme_id uuid REFERENCES deck_themes(id);
ALTER TABLE deck_jobs ADD COLUMN IF NOT EXISTS theme_id uuid REFERENCES deck_themes(id);
