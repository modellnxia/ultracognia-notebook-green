# Especificação de tela — Gerador de Slides (frontend)

> Versão com design publicada como referência visual: https://claude.ai/code/artifact/9abf30c3-95f4-411a-a1b6-2ff3894f8227
> Escrito em 2026-08-19. Complementa [`HANDOFF_FRONTEND.md`](HANDOFF_FRONTEND.md) — aquele é o contrato técnico (endpoints/JSON/proxy); este é o comportamento da tela em si.

## O que a tela faz

Duas partes: uma **lista dos decks já gerados** por aquele usuário (persistida no backend — sobrevive a logout, troca de aparelho, limpar dados do navegador) e um **campo de entrada** pra gerar um novo — o usuário cola o editorial completo (texto puro, sem formatação obrigatória) e aciona a geração. Todo o resto — layout, tema de cor, imagens, diagramas, montagem do PDF/PPTX — acontece no backend, sem escolha manual nesta v1.

## Fluxo, passo a passo

0. **Ao entrar na tela (ou logar de novo)**: chama `GET /decks` (com o `user_id` da sessão, via proxy) e mostra a lista de decks já gerados — mais recente primeiro, cada um com status e um resumo do editorial. Isso resolve o problema de antes: perder a sessão não perdia mais o acesso aos decks, porque a lista mora no backend, não no navegador.
1. Usuário clica em "Gerar novo" (ou a lista já convive com o campo de entrada sempre visível, ver seção 04) e cola o editorial no campo de texto (textarea grande, tipo chat). Botão "Gerar slides" desabilitado enquanto vazio.
2. Usuário aciona a geração (Enter ou clique). Chama `POST /decks` via proxy do backend do frontend — responde em <1s, é só a criação do job.
3. Campo trava, tela entra em "Processando". O editorial enviado continua visível, mas não editável. O novo job já aparece no topo da lista, com status "processando".
4. Tela consulta `GET /decks/{id}` a cada 3–5s.
5. Etapas avançam: **structure → assets → render → qa** — 4 estágios discretos, não percentual contínuo. **Atenção a decks grandes**: um editorial que gera muitos slides com imagem pode levar vários minutos na etapa de imagens (a resolução ainda é sequencial, uma de cada vez) — vale um aviso do tipo "isso pode levar alguns minutos pra editoriais longos", pra gerenciar expectativa.
6. Concluído: aparecem duas opções de download — **PDF** e **PPTX** (PowerPoint editável). Se o QA encontrou algo, mostra aviso não bloqueante (deck continua baixável nos dois formatos). O item na lista atualiza pra "concluído".
7. Usuário baixa no formato que quiser (`GET /decks/{id}/download?format=pdf` ou `?format=pptx` — os dois já estão prontos, não precisa esperar de novo) a qualquer momento, inclusive dias depois, direto da lista — ou clica "Gerar outro" (limpa o campo).

**Sobre a escolha de formato**: os dois arquivos são gerados juntos, sempre — não existe pergunta "PDF ou PPTX?" antes de começar a gerar. A escolha é só na hora de baixar (dois botões, ou um menu "Baixar como ▾"), porque gerar os dois não tem custo extra (nenhum dos dois passa por IA).

## As quatro etapas (rótulos sugeridos pro usuário final)

| Etapa técnica | Rótulo sugerido | O que faz |
|---|---|---|
| `structure` | Estruturando conteúdo | Lê o editorial e decide slides, layout e conteúdo de cada um |
| `assets` | Gerando imagens e diagramas | Gera as imagens/diagramas que o editorial pediu |
| `render` | Montando PDF | Monta o PDF final, 16:9 |
| `qa` | Verificando qualidade | Confere texto cortado, contraste de cor, imagens que falharam |

## Estados da tela

- **Lista carregando** — nada renderizado ainda enquanto `GET /decks` não responde (deve ser rápido, é só uma consulta).
- **Lista vazia** — usuário nunca gerou nada: mostra só o campo de entrada, sem seção de lista (ou com uma mensagem simples tipo "Nenhum deck gerado ainda").
- **Lista com itens** — cada linha: resumo do editorial (`editorial_preview`), data, status (pill colorida), e — se `completed` — os dois botões de download direto na linha, sem precisar abrir o item.
- **Vazio** (campo de geração) — textarea em branco, botão desabilitado.
- **Enviando** — entre o clique e a resposta do `POST /decks`.
- **Processando** — stepper ativo (uma das 4 etapas em destaque), campo bloqueado, sem botão de cancelar nesta v1. O item correspondente na lista mostra o mesmo status.
- **Concluído, sem avisos** — botões "Baixar PDF" e "Baixar PPTX" (os dois já prontos) + "Gerar outro".
- **Concluído, com avisos** — mesmos dois botões de download + aviso expansível ("N pontos de atenção neste deck", lista de `issues` do QA). Nunca bloqueia.
- **Falhou** — mensagem baseada no `error` da etapa que falhou + botão "Tentar novamente" (reenvia o mesmo editorial). Aparece como status "falhou" na lista também.

## Dados de entrada/saída (resumo — JSON completo em HANDOFF_FRONTEND.md)

| | |
|---|---|
| entra | `editorial_text` — texto puro colado pelo usuário |
| sai | PDF **e** PPTX (os dois sempre gerados), cada um via URL assinada (1h) + `issues[]`/`issue_count` opcional do QA |
| lista | `GET /decks` (com `user_id` da sessão) — mais recente primeiro, paginado (`limit`/`offset`), com `editorial_preview` truncado |
| polling | a cada 3–5s até `status` virar `completed` ou `failed` |
| auth | sessão normal do app (ver pendência abaixo) |

## Fora de escopo nesta v1

- Preview slide a slide antes do PDF pronto.
- Busca/filtro na lista de decks (por status, data, texto) — a listagem em si já existe (`GET /decks`), só não tem filtro ainda.
- Edição do deck já gerado (o caminho é ajustar o editorial e gerar de novo).
- Cancelar geração em andamento.

## Pendências antes de implementar

1. **Decisão de produto em aberto**: tela atrás de sessão normal (qualquer usuário logado) ou restrita a administrador? Suposição usada aqui: sessão normal — confirmar antes de codar a proteção de rota.
2. **Configuração de ambiente**: URL pública do serviço + API key de servidor-a-servidor precisam vir do time de backend antes de apontar o proxy pra qualquer ambiente além do de teste.
