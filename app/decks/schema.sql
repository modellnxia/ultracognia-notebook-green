-- Schema do módulo de geração de decks por IA.
-- Aplicado manualmente (não existe ferramenta de migração no projeto ainda —
-- ver pendência #12 no CLAUDE.md). Mantido aqui como referência de portabilidade:
-- na extração pro projeto final, isso vira a base de uma migração de verdade.

CREATE TABLE IF NOT EXISTS deck_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES users(id),
    client_id uuid REFERENCES clients(id),  -- dono da marca/tema; nullable até isso entrar em uso
    editorial_text text NOT NULL,  -- colado pelo usuário no chat da tela de geração; dado de entrada, não saída de etapa
    llm_provider varchar,  -- gemini | deepseek | openrouter; NULL = fallback automático de sempre (2026-08-20, combo de escolha na tela)
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
