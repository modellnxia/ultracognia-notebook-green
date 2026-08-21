# Handoff — Gerador de Slides por IA, integração com o frontend

> Escrito em 2026-08-18 para o time do `ultracognia-frontend-green`. Documenta o que já está pronto e testado no backend (`ultracognia-notebook-green`, módulo `app/decks/`) e como consumir a partir de lá. Ver [`README.md`](README.md) desta pasta para o detalhe técnico interno do pipeline — este documento é só sobre a borda de integração. Para o comportamento da **tela em si** (fluxo, estados, o que mostrar em cada momento), ver [`UI_SPEC_FRONTEND.md`](UI_SPEC_FRONTEND.md) — este aqui é só o contrato técnico (endpoints/JSON/proxy).

## 1. Regra de ouro: o browser nunca fala com este serviço

Mesmo padrão que o `ultracognia-frontend-green` já usa pra falar com o Cérebro (`api/app/api/prompt.py`, env vars `URL_API_CEREBRO`/`API_KEY_CEREBRO`): quem chama este serviço é o **backend do frontend** (`api/`), nunca o navegador direto. A tela do gerador de slides chama o backend do frontend (com o JWT normal de sessão), e é o backend do frontend quem repassa a chamada pra cá usando uma API key de servidor-para-servidor.

Isso existe porque a API key deste serviço (`x-api-key`, ver `main.py` → `validar_acesso`) dá acesso total aos endpoints — não pode vazar pro navegador de jeito nenhum (inspecionar o Network tab do DevTools exporia a chave se ela fosse usada direto do frontend React).

**Variáveis novas sugeridas no `api/.env` do frontend** (mesmo padrão de nomenclatura do Cérebro):

```
URL_API_DECKS=https://notebookdev-modellnxdev.up.railway.app
API_KEY_DECKS=<pedir ao time de backend — mesmo valor da API_KEY configurada no notebook_dev>
```

⚠️ **Essa URL é do ambiente de teste/dev (`modellnx_dev`)** — criada em 2026-08-19, é a primeira vez que este serviço tem domínio público. A promoção pra produção (`modellnx_blue`) ainda não aconteceu (ver seção 9) — não apontar direto pra ela sem confirmar com o time de backend antes.

## 2. Autenticação da tela em si — decisão em aberto

Este documento assume que a tela do gerador de slides fica **atrás de sessão normal** (`get_current_user_session`, mesmo dependency de `/prompt/chat`), não necessariamente `require_admin` (que hoje só libera pro Ramon Muliterno, ver `api/app/api/admin.py`). Se o produto quiser isso restrito a admin, é só trocar a dependency no endpoint proxy abaixo — decisão de produto, não técnica, meu chute é sessão normal, mas **confirmar antes de implementar**.

## 3. Endpoints deste serviço (backend do frontend chama estes três)

Autenticação: header `x-api-key` em todas as chamadas (case-insensitive, mas o resto do código usa `X-API-Key` — ver exemplo na seção 5).

### `POST /decks` — cria um job de geração

```jsonc
// Request
{
  "user_id": "13d32c21-0432-43b7-b787-cae9eb4f42b2",   // uuid do usuário dono do deck
  "editorial_text": "texto completo colado pelo usuário no chat...",  // obrigatório, min 1 char
  "client_id": null,   // uuid opcional — reservado pra white-label (tema por cliente), ainda não usado por nenhum agente
  "llm_provider": null,   // "gemini" | "deepseek" | "openai" | omitir/null. Ver seção 3.1 (openrouter removido do combo em 2026-08-21)
  "apply_style_guardrails": false   // checkbox na tela. Ver seção 3.2 — ⚠️ default FALSE (invertido em 2026-08-21)
}
```

```jsonc
// Response 201
{
  "id": "3e903ed7-021b-48c4-bf6f-31a539825031",
  "status": "pending",
  "cost_cents": 0,
  "created_at": "2026-08-20T10:00:00Z",
  "llm_provider": null,
  "apply_style_guardrails": false,
  "steps": [
    {"step": "structure", "status": "pending", "attempt": 0, "output_ref": null, "error": null},
    {"step": "assets",    "status": "pending", "attempt": 0, "output_ref": null, "error": null},
    {"step": "render",    "status": "pending", "attempt": 0, "output_ref": null, "error": null},
    {"step": "qa",        "status": "pending", "attempt": 0, "output_ref": null, "error": null}
  ]
}
```

**Responde na hora** — quem processa de fato é um poller assíncrono do lado de cá (roda a cada ~5s). O front não espera a geração terminar nessa chamada.

### 3.1. Escolha de provider de IA (`llm_provider`) — combo na tela

⚠️ **Mudança de contrato em 2026-08-21**: `openrouter` foi **removido** do combo (decisão do cliente) e `openai` **entrou** no lugar. Se o combo já estava implementado com as 3 opções antigas (`gemini`/`deepseek`/`openrouter`), precisa trocar `openrouter` por `openai` — mandar `"openrouter"` agora dá **422** (schema não aceita mais esse valor).

Combo final: `gemini` | `deepseek` | `openai` | automático (omitir campo/`null`). Cada opção roda **ponta a ponta num provider só, texto E imagem juntos** — não é só o texto que muda:

- **`"gemini"`** — Gemini gera o texto/layout **e** a imagem. Nenhuma chamada à OpenAI nesse caminho.
- **`"deepseek"`** — DeepSeek gera o texto/layout; a imagem é gerada pela **OpenAI** (DeepSeek não tem produto de imagem próprio — é assim de propósito, não é bug).
- **`"openai"`** — OpenAI gera o texto/layout **e** a imagem (novo em 2026-08-21 — o texto da OpenAI também é multimodal, recebe as mesmas imagens de referência que o Gemini recebe).
- **Omitir o campo (ou `null`)** — comportamento de fallback automático: tenta o provider de maior prioridade, cai pro próximo se falhar. A imagem, nesse caso, usa o default do backend (`DECK_IMAGE_PROVIDER`).
- Valor explícito e ele falhar → o job vai pra `failed` (não cai silenciosamente pra outro — de propósito, pra vocês conseguirem comparar de verdade qual provider entrega o quê).
- A resposta (`GET /decks/{id}` e cada item de `GET /decks`) mostra qual `llm_provider` foi usado — útil pra exibir um selo tipo "Gerado com: DeepSeek" na lista/no card do deck.

### 3.2. Checkbox de guardrail visual (`apply_style_guardrails`) — ⚠️ default invertido em 2026-08-21 (2)

Controla se o "rulebook" visual interno (vocabulário de composição — posição de ilustração, estilo de painel, etc. — mais o piso de qualidade/densidade que a gente aplica) entra na geração ou não.

⚠️ **Se a tela já tinha isso implementado com o checkbox começando MARCADO, precisa trocar pra começar DESMARCADO** — o default virou `false` (decisão do usuário, 2026-08-21): achado real em produção, um job com o checkbox aparentando desligado ainda assim aplicou o guardrail (o editorial tinha paleta clara própria e foi rejeitado pela validação de fundo escuro) — o mais provável é o campo não estar sendo enviado ainda nesse ponto, caindo no default antigo (`true`). Efeito colateral (fora do nosso alcance verificar daqui): se o toggle da tela estiver com a lógica invertida (marcado = manda `false`, ou vice-versa), isso continua dando o mesmo sintoma mesmo com o default do backend corrigido — vale conferir o wiring do componente.

- **`false`** (default — se omitir o campo, vale isso) — o editorial do usuário é extraído direto pro nosso schema de JSON e mandado pro agente, sem nenhuma opinião de estilo nossa — inclusive paleta clara é aceita se o editorial pedir uma. É o comportamento padrão agora.
- **`true`** — só quando o usuário MARCAR explicitamente: o rulebook interno entra (vocabulário de composição, exemplos de referência), fundo do slide é obrigatoriamente escuro (validado automaticamente).

Sugestão de UI: um toggle/checkbox simples, **começando desmarcado**, com um tooltip tipo "Aplicar padrão visual da Ultracognia" — texto exato fica a critério de vocês, é só pra dar a ideia do que a opção faz quando marcada.

### `GET /decks/{job_id}` — consulta status (pra fazer polling)

Mesmo formato de resposta do `POST`. `status` do job é `pending` → `running` → `completed` ou `failed`. Cada item de `steps` tem seu próprio `status`; um `error` preenchido explica o que deu errado naquela etapa especificamente. Ganhou o campo `created_at` (2026-08-19).

**Fluxo de polling recomendado**: depois do `POST /decks`, faça `GET /decks/{job_id}` a cada ~3-5s até `status` virar `completed` ou `failed`. Use os `steps[].status` pra mostrar uma barra de progresso com 4 estágios (structure → assets → render → qa) — são estágios discretos reais, não um percentual contínuo.

### `GET /decks?user_id={uuid}&limit=&offset=` — lista os jobs de um usuário (novo, 2026-08-19)

Resolve o problema de "perdi a sessão e não sei mais o ID dos meus decks" — antes disso, a única forma de recuperar um job era guardar o ID em algum lugar do lado do frontend (localStorage, por exemplo), o que não sobrevive a logout/limpeza de dados/trocar de aparelho. Agora o backend é a fonte de verdade: consulta sempre por `user_id`, não depende de nada guardado no navegador.

```jsonc
// Request: GET /decks?user_id=13d32c21-0432-43b7-b787-cae9eb4f42b2&limit=50&offset=0
// Response 200
{
  "jobs": [
    {
      "id": "3e903ed7-021b-48c4-bf6f-39a540825031",
      "status": "completed",
      "cost_cents": 0,
      "created_at": "2026-08-19T23:00:00Z",
      "llm_provider": "deepseek",
      "apply_style_guardrails": true,
      "editorial_preview": "Relatório: Modernização da Cadeia de Suprimentos via IA Prediti…"
    }
  ]
}
```

- `user_id` é obrigatório — mesmo espírito do `POST /decks`, quem garante que é o usuário certo é a sessão já validada no backend do frontend, não este serviço.
- `limit`/`offset` — paginação, mesmo padrão que o `GET /admin/conversations` de vocês já usa (`limit` 1-200, default 50).
- `editorial_preview` já vem truncado (80 caracteres) pelo próprio banco — não existe um campo "título" separado no contrato, isso é o suficiente pra identificar qual deck é qual numa lista. Pra ver tudo, é só abrir o job (`GET /decks/{id}`).
- Mais recentes primeiro (`created_at DESC`).
- **Uso sugerido na tela**: ao carregar a tela (ou depois de logar de novo), chamar este endpoint com o `user_id` da sessão, renderizar a lista, e cada item abre pro fluxo normal de status/download já documentado. Ver `UI_SPEC_FRONTEND.md` atualizado.

### `GET /decks/{job_id}/download?format=pdf|pptx` — baixa o deck pronto

Redireciona (302/307) pra uma URL assinada do Supabase Storage, válida por 1h, gerada na hora (nunca é a mesma URL duas vezes). Só funciona depois que a etapa `render` estiver `completed` — antes disso responde `404`.

**Os dois formatos já estão sempre prontos ao mesmo tempo** (desde 2026-08-19) — não existe "escolher o formato antes de gerar". O parâmetro `format` decide só qual arquivo baixar agora: `pdf` (default, se omitir o parâmetro) ou `pptx` (PowerPoint editável, mesmo conteúdo). Um valor fora desses dois responde `422` (validação de query param). Ideal pra UI: dois botões/opção de menu — "Baixar PDF" e "Baixar PPTX" — sem precisar perguntar nada antes da geração.

## 4. Tratando falha

Se `status == "failed"`, olhe o `error` de cada step pra saber o motivo. Casos conhecidos hoje:
- **Editorial vazio/etapa `structure` falhou 3x** — o LLM não conseguiu gerar um `DeckSpec` válido a partir do texto. `error` traz o motivo.
- **Assets** nunca falham o job inteiro — se uma imagem/diagrama não resolver, o slide fica com um placeholder visual no PDF, mas o job segue e completa normalmente. Não é preciso tratar isso como erro pro usuário.
- **Estouro de custo** (`DECK_JOB_MAX_COST_CENTS`, hoje US$ 5/job) — todas as etapas ainda pendentes viram `failed` com o mesmo `error` explicando o teto excedido.

## 5. Exemplo de implementação (backend do frontend, `api/app/api/decks.py` — novo arquivo)

Segue exatamente o padrão já usado em `prompt.py` pro Cérebro:

```python
import os
import httpx
from fastapi import APIRouter, Depends, HTTPException
from app.api.auth import get_current_user_session

router = APIRouter(prefix="/decks", tags=["decks"])

def _deck_service_headers() -> dict:
    api_key = os.getenv("API_KEY_DECKS")
    if not api_key:
        raise HTTPException(status_code=500, detail="Serviço não configurado corretamente")
    return {"X-API-Key": api_key, "Content-Type": "application/json"}

@router.post("")
async def criar_deck(body: dict, user=Depends(get_current_user_session)):
    url = os.getenv("URL_API_DECKS")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{url}/decks",
            headers=_deck_service_headers(),
            json={**body, "user_id": user["sub"]},
            timeout=30.0,
        )
    resp.raise_for_status()
    return resp.json()

@router.get("/{job_id}")
async def status_deck(job_id: str, user=Depends(get_current_user_session)):
    url = os.getenv("URL_API_DECKS")
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{url}/decks/{job_id}", headers=_deck_service_headers(), timeout=30.0)
    resp.raise_for_status()
    return resp.json()

@router.get("")
async def listar_meus_decks(
    limit: int = 50, offset: int = 0, user=Depends(get_current_user_session)
):
    """user['sub'] já vem da sessão — a tela não precisa (nem deve) passar user_id manualmente."""
    url = os.getenv("URL_API_DECKS")
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{url}/decks",
            params={"user_id": user["sub"], "limit": limit, "offset": offset},
            headers=_deck_service_headers(), timeout=30.0,
        )
    resp.raise_for_status()
    return resp.json()

@router.get("/{job_id}/download")
async def baixar_deck(job_id: str, format: str = "pdf", user=Depends(get_current_user_session)):
    """Repassa o redirect — ou baixa aqui e repassa os bytes, se preferir não expor a URL assinada do Supabase direto ao browser."""
    url = os.getenv("URL_API_DECKS")
    async with httpx.AsyncClient(follow_redirects=False) as client:
        resp = await client.get(
            f"{url}/decks/{job_id}/download",
            params={"format": format},
            headers=_deck_service_headers(), timeout=30.0,
        )
    if resp.status_code in (302, 307):
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=resp.headers["location"])
    raise HTTPException(status_code=resp.status_code, detail="Deck ainda não disponível")
```

## 6. Layouts possíveis (pra referência visual, se a UI quiser mostrar preview)

O `structure` (LLM) escolhe entre 7 layouts fechados por slide — não dá pra vir nenhum outro valor: `cover`, `section-break`, `title-bullets`, `two-column`, `diagram-full`, `closing`, `infographic` (novo em 2026-08-19 — eyebrow + título + subtítulo + ilustração + 2-4 painéis estruturados + citação de rodapé; é o layout padrão pra conteúdo analítico/estratégico agora). Isso não aparece na resposta da API hoje (o `DeckSpec` inteiro fica só internamente, entre as etapas) — se a UI quiser mostrar um preview slide-a-slide antes do PDF pronto, isso é uma extensão nova a pedir, não existe endpoint pra isso ainda.

## 7. QA visual — o que vem no `output_ref` da etapa `qa`

Desde 2026-08-18 a etapa `qa` roda checks reais (determinísticos, sem IA — overflow de texto, contraste de cor, asset não resolvido). O `output_ref` desse step (já visível na resposta normal de `GET /decks/{job_id}`, dentro de `steps`) é uma string JSON com este formato:

```jsonc
{
  "issues": [
    {"slide_id": "slide-2", "kind": "overflow", "message": "O conteúdo deste slide ultrapassa a área visível (1280x720) — pode aparecer cortado no PDF."},
    {"slide_id": null, "kind": "low_contrast", "message": "Contraste entre 'text' (#EEEEEE) e 'background' (#FFFFFF) é 1.2:1 — abaixo do mínimo recomendado (3.0:1)."}
  ],
  "issue_count": 2
}
```

`slide_id` é `null` quando o problema é do tema inteiro (contraste), não de um slide específico. **Isso nunca bloqueia o download** — é decisão de produto (2026-08-18): o PDF fica disponível normalmente mesmo com `issue_count > 0`. Cabe à tela decidir se mostra um aviso ("N pontos de atenção neste deck, revisar antes de enviar ao cliente") — não é obrigatório tratar isso na v1 da tela, mas o dado já está disponível se quiserem.

## 8. O que NÃO existe ainda (não assumir)

- **Sem busca/filtro na listagem** — `GET /decks` lista tudo por `user_id`, paginado, mas não tem filtro por status/data/texto ainda. Se a tela precisar disso, é extensão nova.
- **QA visual é só determinístico** — não há revisão por IA (ex.: "essa imagem combina com o texto?"); só overflow/contraste/asset não resolvido, ver seção 7.
- **Geração de imagem real depende de billing habilitado no Gemini** — hoje a chave de teste está no tier gratuito (cota 0 pro modelo de imagem). Até isso ser resolvido, decks gerados no ambiente de teste podem ter placeholder no lugar de imagem (diagramas via Mermaid funcionam normalmente, sem essa dependência).
- **Branding por cliente (`client_id`)** — o campo existe no contrato mas nenhum agente usa ele ainda pra fixar tema/logo; hoje o LLM escolhe o tema livremente a cada geração.

## 9. Sobre a URL de produção / ambientes

Este módulo roda dentro do **mesmo serviço** que já gera os relatórios semanais (`notebook_dev` no Railway, projeto `MODELLNX-DEV`) — não é um serviço novo, os endpoints `/decks/*` só foram adicionados ao mesmo backend. Hoje só o environment de teste/dev está validado ponta a ponta com dado real; a promoção pra produção (`modellnx_blue`) ainda não aconteceu — pedir confirmação de qual URL usar antes de apontar qualquer ambiente de frontend que não seja teste/dev.
