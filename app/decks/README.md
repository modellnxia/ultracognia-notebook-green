# Gerador de slides por IA — módulo à parte

Este módulo (`app/decks/`) foi construído **de propósito** com baixo acoplamento
ao resto do `ultracognia-notebook-green`. Estamos em fase de POC com o cliente
— quando ela terminar, a intenção é **extrair essa pasta inteira** para o
projeto final, com o mínimo de fricção possível.

## Regra de organização (diferente do resto do app)

O resto do projeto organiza por *tipo* (`app/models/`, `app/services/`,
`app/repositories/`, `app/routers/`). Este módulo organiza por *feature* —
tudo relacionado a deck vive dentro de `app/decks/`, incluindo seu próprio
`models.py`, `service.py`, `router.py` etc., conforme as fatias forem sendo
implementadas. Isso é deliberado: o objetivo é "copiar a pasta e sair
funcionando", não seguir a convenção do host.

## Contrato de portabilidade — o que este módulo espera de fora dele

Conforme as fatias forem avançando, esta lista cresce. Hoje (fatia 1):

- **Nada.** `models.py` é Pydantic puro, zero dependência do resto do app.

Quando a IA/banco/storage entrarem (fatias 2+), a lista vai incluir algo como:
uma conexão `asyncpg` (compartilhada com o host via injeção, não importada
direto), leitura de histórico de conversa (reaproveita
`ConversationMessageRepository` do host — ponto de acoplamento intencional,
não seu módulo), e variáveis de ambiente próprias (prefixo `DECK_*` /
`GEMINI_*`, não misturadas com as do resto do app).

## Estado atual

- ✅ **Fatia 1** — `models.py`: `DeckSpec`, 6 layouts fechados, blocos de
  conteúdo tipados, validação (Pydantic).
- ✅ **Fatia 2** — `repository.py`/`poller.py`/`router.py`: tabelas `deck_jobs`/
  `deck_job_steps` (Supabase de teste), poller registrado no mesmo
  `AsyncIOScheduler` do backup (sem infra nova), `POST /decks` +
  `GET /decks/{id}`. Retomada **provada de verdade** com dois processos
  Python separados (um cria o job e processa etapas falsas e sai; outro,
  interpretador novo, sem memória nenhuma do primeiro, retoma exatamente de
  onde parou — só o banco carrega o estado). Disjuntor de custo por job
  (`enforce_cost_cap` / `DECK_JOB_MAX_COST_CENTS`, US$ 5,00 por padrão)
  adicionado na revisão de 2026-08-18 — aborta o que resta se o gasto
  acumulado estourar o teto.
- ✅ **Fatia 3** — `render.py` + `templates/deck.html.jinja2`: `DeckSpec` → HTML,
  um `<section>` de 1280×720 (16:9) por slide, pronto pra virar PDF na fatia 4.
  Tema (cor/fonte/logo) só do `DeckSpec`, nunca improvisado. Todo texto de
  usuário passa por `escape()` (testado contra injeção de HTML/script).
  Assets ainda são um placeholder visual — resolução de verdade é fatia 6.
  15 testes automatizados + verificação visual real (screenshot via
  Playwright dos 6 layouts, incluindo capa com logo e slide de duas
  colunas).
- ✅ **Fatia 4** — `pdf.py` (HTML→PDF via Playwright) + `storage.py` (cliente
  do Supabase Storage via `httpx` puro, sem SDK novo) + `GET /decks/{id}/download`
  (URL assinada gerada na hora, nunca guardada — expira em 1h por design).
  **Provado ponta a ponta com dado real**: job criado no banco de teste →
  poller processou as etapas → PDF real gerado e subido no Supabase Storage →
  URL assinada → download via HTTP confirmado (`200 OK`, PDF válido). Job de
  teste limpo depois (banco + storage).
- ✅ **Revisão de pipeline (2026-08-18)** — o stakeholder definiu que a entrada
  é o **editorial completo em texto puro**, colado pelo usuário no chat da
  tela de geração (sem JSON, sem schema de entrada). Isso eliminou as etapas
  `context`/`narrative` (não há mais conversa pra resumir nem narrativa pra
  inventar — o editorial já é o conteúdo final). Pipeline passou de 6 pra
  **4 etapas**: `structure → assets → render → qa`. `deck_jobs` ganhou a
  coluna `editorial_text` (obrigatória, gravada na criação do job).
- ✅ **Fatia 5 (camada de LLM + `structure` real)** — `llm/providers.py` lê a
  tabela `providers` já existente na plataforma (reaproveitada, não criada
  do zero) pra descobrir provedores ativos em ordem de prioridade;
  `llm/client.py` fala com Gemini nativo (`generateContent`) e formato
  OpenAI-compatível (DeepSeek/OpenRouter via `/chat/completions`), tentando
  cada provedor até um funcionar (`generate_text`). `structure.py` monta o
  prompt (editorial + guia textual de layout — placeholder até o stakeholder
  entregar as imagens de referência few-shot — + JSON Schema do `DeckSpec`),
  chama o LLM e valida a resposta, retentando até 3x com o erro de validação
  anexado ao prompt se o modelo errar o formato. `steps.py::run_structure`
  lê o `editorial_text` do job e grava o `DeckSpec` real gerado;
  `steps.py::run_render` passou a consumir esse `DeckSpec` real (não mais um
  deck fixo). Hoje só o provedor Gemini tem chave configurada (`GEMINI_API_KEY`
  no `.env`) — DeepSeek/OpenRouter entram no fallback assim que tiverem chave,
  sem mudança de código. 65 testes automatizados no total do módulo (suíte
  completa passando, incluindo os ajustes desta revisão). **Provado ponta a
  ponta com dado real** (banco de teste, `wjmocpjkuoqxcdizejgr`): editorial de
  exemplo → `run_structure` chamou o Gemini de verdade (1563 tokens entrada /
  704 saída) e devolveu um `DeckSpec` válido de 5 slides com layouts variados
  (cover/title-bullets/two-column/closing) e tema coerente → `run_render`
  consumiu esse `DeckSpec` real (não mais fixo) e gerou um PDF real (32KB) →
  upload no Supabase Storage → URL assinada → download via HTTP confirmado
  como PDF válido. Dado de teste limpo depois (job + objeto no storage).
- ✅ **Fatia 6 (assets reais — imagem + diagrama)** — `run_assets` deixou de
  ser falso. Dois tipos de asset, resolvidos de formas diferentes:
  - `kind="image"` → `llm/images.py::generate_image()`, Gemini nativo
    ("Nano Banana", `GEMINI_IMAGE_MODEL`, default `gemini-3.1-flash-image`).
    Fora do fallback multi-provider de `client.py` de propósito — hoje só o
    Gemini gera imagem entre os providers cadastrados.
  - `kind="diagram"` → `diagrams.py::render_diagram_png()`, Mermaid
    renderizado **localmente** via Playwright (mesmo Chromium já usado pro
    PDF) — `mermaid.min.js` (10.9.1) vendorizado dentro do módulo
    (`static/mermaid.min.js`), embutido inline no HTML, **sem** depender de
    CDN externo em tempo de execução (mantém a promessa de portabilidade).
  - `structure.py` ganhou a regra correspondente no prompt: o LLM inclui um
    asset `kind="diagram"` (sintaxe Mermaid) quando o editorial descreve um
    fluxo/processo, além da regra de imagem que já existia.
  - Cada asset é resolvido com **isolamento de falha** (mesmo padrão do
    `backup_job.py`): se UM asset falhar (rede, quota, Mermaid inválido —
    captura `Exception` largo de propósito, não só as exceptions próprias),
    loga e mantém o placeholder pra aquele slide, sem derrubar o job
    inteiro. `run_render` passou a ler o `DeckSpec` da etapa `assets` (não
    mais `structure` diretamente) e gera URL assinada pra cada asset
    resolvido antes de montar o HTML — o Chromium busca a imagem por HTTP
    normalmente. Template ganhou `<img>` real quando o `ref` já é uma URL;
    mantém o placeholder tracejado pros não resolvidos (comportamento antigo
    intacto).
  - **Validado ponta a ponta com dado real**: editorial pedindo
    explicitamente uma imagem de capa E um diagrama de fluxo → Gemini gerou
    um `DeckSpec` com os dois tipos de asset → diagrama renderizou de
    verdade (Mermaid/Playwright, sem custo de API) e foi embutido no PDF
    final como `<img>` real. **Achado real durante a validação**: a chave
    `GEMINI_API_KEY` está no tier gratuito, que tem `limit: 0` req/dia pro
    modelo de imagem (`gemini-3.1-flash-image`) — confirmado na resposta
    bruta da API (`RESOURCE_EXHAUSTED`, `GenerateRequestsPerDayPerProjectPerModel-FreeTier`,
    `limit: 0`). Não é bug de código: geração de imagem só funciona com
    billing habilitado no projeto do Google Cloud por trás dessa chave.
    Isso, por sua vez, **provou o isolamento de falha funcionando de
    verdade**: o 429 foi capturado, o slide da imagem ficou com o
    placeholder, e o job completou com sucesso mesmo assim (diagrama +
    resto do deck saíram normais). Bug real encontrado e corrigido nessa
    mesma validação: o `except` original só pegava as exceptions próprias
    do módulo (`ImageGenerationError`/`DiagramRenderError`) — um
    `httpx.HTTPStatusError` cru (como esse 429) escapava e derrubava o job
    inteiro; ampliado pra `except Exception`.
  - **Desbloqueado com um provider alternável**: `DECK_IMAGE_PROVIDER`
    (default `gemini`) — `pollinations` usa a API pública do
    [Pollinations.ai](https://pollinations.ai), gratuita, sem chave nem
    cadastro (`_generate_image_pollinations` em `llm/images.py`). **Só pra
    POC/teste** — sem SLA, sem garantia de uptime, moderação de conteúdo
    mais solta; não é o provider pensado pra produção com o cliente final.
    Basta trocar a env var de volta pra `gemini` quando o billing for
    resolvido, sem mudar código. **Validado ponta a ponta com os dois
    assets reais dessa vez** (`DECK_IMAGE_PROVIDER=pollinations`): imagem E
    diagrama resolvidos de verdade, os dois embutidos como `<img>` no PDF
    final baixado.
  - **Pendência de negócio, não de código**: habilitar billing na chave do
    Gemini (ou trocar por uma chave paga) antes de usar o provider `gemini`
    de verdade em produção.
  - 84 testes automatizados no total do módulo.
- ✅ **Fatia 7 (QA visual, checks determinísticos)** — decisão do usuário em
  2026-08-18: sem IA (custo/quota zero, rápido) e **nunca bloqueia** o job —
  mesma filosofia de resiliência do resto do pipeline. `qa.py::check_deck`
  roda três checks sobre o `DeckSpec` da etapa `assets`:
  1. **Overflow de texto** — medido de verdade no Chromium via Playwright
     (`scrollHeight`/`scrollWidth` de cada `.slide` contra a caixa fixa de
     1280×720), não estimado por contagem de caracteres.
  2. **Contraste de cor** — fórmula de luminância relativa do WCAG 2.0,
     aplicada aos dois pares que o template realmente usa como texto
     (`text`/`background` nos slides normais, `background`/`primary` nos de
     tela cheia). Limiar 3.0:1 (texto grande — todo o texto deste deck se
     qualifica, corpo é 22px+).
  3. **Asset não resolvido** — slide cujo `asset.ref` ainda é o prompt/
     sintaxe original (a etapa `assets` não conseguiu resolver).
  - `steps.py::run_qa` grava o relatório (`{"issues": [...], "issue_count": N}`)
    como `output_ref` da própria etapa — já visível no `GET /decks/{id}`
    existente, sem mudança de schema/endpoint. Documentado em
    [`HANDOFF_FRONTEND.md`](HANDOFF_FRONTEND.md).
  - 98 testes automatizados no total do módulo. **Validado ponta a ponta**:
    pipeline completo (`structure→assets→render→qa`) rodado contra o banco
    de teste com dado real — deck limpo saiu com `issue_count: 0` (sem falso
    positivo), e a detecção de problema real (overflow forçado, contraste
    baixo, asset não resolvido) já provada nos testes automatizados com
    Chromium de verdade.
- ✅ **PPTX como segundo formato de saída** (2026-08-19, confirmado como
  necessário pelo usuário — deixou de ser "fora de escopo"). `render`
  agora gera os **dois formatos sempre**, não um escolhido na criação do
  job — a escolha é do usuário na hora do **download**
  (`GET /decks/{id}/download?format=pdf|pptx`, default `pdf`). Motivo de
  gerar os dois sempre: nenhum dos dois depende de LLM (PDF é Playwright,
  PPTX é `python-pptx`, os dois 100% locais/gratuitos), então gerar os dois
  é praticamente de graça — mais simples e mais barato que forçar o
  usuário a decidir antes de ver o resultado, ou reprocessar se mudar de
  ideia.
  - `pptx_render.py` (novo) — constrói o `.pptx` **direto do `DeckSpec`**
    com `python-pptx` (pura Python, sem dependência de serviço externo,
    mesma filosofia de portabilidade do resto do módulo) — não é um "PDF
    convertido", é um segundo renderizador independente que lê o mesmo
    documento e aplica as mesmas regras de layout/tema, mapeando os 6
    layouts e os 4 tipos de bloco pra formas nativas do PowerPoint
    (textbox, imagem, marcador de lista via manipulação de XML — python-pptx
    não tem API de alto nível pra bullet).
  - Slide 16:9 (13.333×7.5in, mesma proporção do PDF). Assets resolvidos
    (`kind="image"` ou `"diagram"`) viram imagem real embutida — os bytes
    são baixados via `httpx` a partir da mesma URL assinada usada pro HTML
    (`add_picture` do `python-pptx` exige bytes, não aceita URL). Falha no
    download (rede, asset não resolvido) cai no mesmo placeholder de texto
    do HTML — resiliente, não derruba o job.
  - `output_ref` da etapa `render` mudou de um caminho único (string) pra
    `{"pdf": "...", "pptx": "..."}` (JSON) — sem migração de dado antigo
    necessária, esse valor nunca sobrevive além de um job.
  - **Bug real encontrado e corrigido durante a validação manual**:
    `_fetch_image_bytes` não seguia redirect (`httpx.AsyncClient` sem
    `follow_redirects=True` por padrão) — um serviço de imagem que
    redireciona (ex.: picsum.photos) fazia a imagem cair silenciosamente no
    placeholder em vez de embutir de verdade. Corrigido.
  - **Validado ponta a ponta com dado real**: pipeline completo rodado
    contra o banco de teste → PDF e PPTX baixados de verdade via
    `GET /decks/{id}/download` (os dois formatos) → PPTX reaberto com o
    próprio `python-pptx` pra confirmar validade (4 slides, íntegro).
  - 106 testes automatizados no total do módulo.
- ✅ **Layout `infographic` + few-shot visual real (2026-08-19)** — usuário
  comparou nosso resultado com decks reais gerados pelo NotebookLM (3
  referências, "shot1/2/3") e considerou o nosso "inaceitável": sem imagem
  (achado à parte, ver abaixo) e com layouts simples demais. Resposta em
  duas partes:
  1. **7º layout, `infographic`** — eyebrow (categoria) + título + subtítulo
     + ilustração hero + 2-4 painéis estruturados (`PanelListBlock`/`Panel`,
     campo novo no contrato) + citação de rodapé (framework/fonte). Vira o
     layout PADRÃO pra conteúdo analítico/estratégico no prompt do
     `structure`, não uma exceção.
  2. **Few-shot visual de verdade** — fechou o `TODO` que já existia no
     código desde a fatia 5 ("substituir esse guia textual pelas imagens de
     referência few-shot"). Duas das imagens reais do usuário (comprimidas,
     vendorizadas em `static/fewshot/`) são anexadas de verdade na chamada
     ao Gemini (multimodal — `llm/client.py` ganhou suporte a
     `reference_images`, antes só mandava texto).
  - Ilustração deixou de ser opt-in: o prompt agora trata imagem/diagrama
    como padrão de todo slide `infographic`/`diagram-full`, não condicional
    a o editorial mencionar explicitamente.
  - **Achado operacional, não de design**: `DECK_IMAGE_PROVIDER` nunca tinha
    sido configurado em produção (só existia no ambiente de teste local) —
    com `GEMINI_API_KEY` ainda no tier gratuito (cota 0 pro modelo de
    imagem), toda geração de imagem em produção estava caindo no
    placeholder silenciosamente. Corrigido (`pollinations` configurado
    também no `notebook_dev`).
  - Os dois renderizadores (`render.py`/HTML e `pptx_render.py`) ganharam o
    layout novo de forma equivalente — painéis viram cards com barra de
    destaque nos dois, não só visual, o PPTX continua 100% editável.
  - **Validado ponta a ponta com dado real, com o few-shot anexado de
    verdade**: o Gemini escolheu sozinho `infographic` pra 3 dos 5 slides
    (reservando cover/closing pros extremos), preencheu eyebrow/painéis/
    citação plausível referenciando frameworks reais (MIT Sloan, Goldratt),
    e gerou imagem real (via Pollinations) pra todo slide com asset — zero
    placeholder. QA: `issue_count: 0`.
  - 139 testes automatizados no total do módulo.
  - **Gaps conhecidos, não resolvidos ainda**: a relevância temática da
    imagem depende da qualidade do provider (Pollinations é mais solto que
    o Gemini seria com billing habilitado — ainda pendente); a paleta de
    cor é decisão livre do LLM a cada geração, às vezes sai menos refinada
    (ex.: um ciano vibrante numa capa, na validação real) — não há curadoria
    de paleta ainda.
- ✅ **Listagem de decks por usuário (2026-08-19)** — `GET /decks?user_id={uuid}&limit=&offset=`. Resolve um problema real relatado pelo usuário: perder a sessão (logout/troca de aparelho/limpar dados do navegador) fazia "sumir" o acesso aos decks já gerados, porque a única forma de recuperar um job era o frontend ter guardado o ID em algum lugar local. Agora o backend é a fonte de verdade — `DeckJobRepository.list_jobs_for_user` (mais recente primeiro, paginado, `editorial_preview` truncado em 80 caracteres direto no SQL). `DeckJobStatusResponse` também ganhou `created_at`.
  - **Validado com dado real, na rota HTTP de verdade** (não só o repositório): jobs de teste criados no banco, request HTTP real através do router (`AsyncClient` + `ASGITransport`) contra o banco de teste real — 200 OK, ordenação e truncamento corretos, job de teste limpo depois.
  - Documentação de handoff (`HANDOFF_FRONTEND.md`) e especificação de tela (`UI_SPEC_FRONTEND.md` + Artifact) atualizadas e republicadas nos mesmos links, antes mesmo do código estar pronto — pra o time de frontend poder começar a implementar em paralelo.
  - 147 testes automatizados no total do módulo.
- ✅ **Multi-provider com escolha explícita — Gemini/DeepSeek/OpenRouter (2026-08-20)** — pedido do cliente: comparar os providers de IA de propósito, não só confiar no fallback automático. `POST /decks` ganhou `llm_provider` opcional — se informado, usa **só** aquele provider (sem cair pra outro se falhar, de propósito, pra permitir comparação real). `llm/client.py::generate_text` ganhou `preferred_provider`; `structure.py` repassa a escolha do job. Coluna nova `deck_jobs.llm_provider` (migração manual aplicada no banco de teste).
  - **DeepSeek/OpenRouter não recebem as imagens de referência** (só o Gemini é multimodal aqui) — pra não ficarem sem nenhum guia visual, `_FEWSHOT_DESCRIPTION` (texto descrevendo o mesmo padrão: eyebrow, ilustração, painéis, citação) agora é sempre incluída no prompt, reforçando as imagens quando presentes (Gemini) e servindo como único guia quando não (DeepSeek/OpenRouter).
  - **Validado com a chave real do DeepSeek**: `llm_provider="deepseek"` forçado de propósito, chamada real (confirmado nos logs: nunca tentou o Gemini) — mesmo sem imagem, escolheu `infographic` corretamente em 3 de 4 slides, preencheu eyebrow/painéis.
  - Documentação atualizada e republicada nos mesmos links **antes** da implementação terminar, a pedido do usuário, pra o frontend poder construir o combo em paralelo.
  - 159 testes automatizados no total do módulo.
  - **Pendências**: chave do OpenRouter ainda não existe (cliente gerando) — endpoint já aceita a opção, só vai falhar até a chave existir. `openrouter` tem prioridade 3 na tabela `providers`, nunca testado de verdade neste pipeline ainda.

## Integração com o frontend (`ultracognia-frontend-green`)

O frontend **não chama esse módulo direto do navegador**. Segue o mesmo
padrão que o `ultracognia-frontend-green` já usa pra falar com o Cérebro
(`api/app/api/prompt.py`): o browser chama o backend próprio do frontend
(`api/`, sessão JWT normal), que por sua vez chama esse serviço aqui usando
`x-api-key` (que nunca é exposta ao browser). Documento de handoff completo
(endpoints, contrato, exemplo de código, o que ainda não existe) em
[`HANDOFF_FRONTEND.md`](HANDOFF_FRONTEND.md), escrito em 2026-08-18.
