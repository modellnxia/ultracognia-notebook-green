# Especificação de tela — Gerador de Slides (frontend)

> Versão com design publicada como referência visual: https://claude.ai/code/artifact/9abf30c3-95f4-411a-a1b6-2ff3894f8227
> Escrito em 2026-08-19. Complementa [`HANDOFF_FRONTEND.md`](HANDOFF_FRONTEND.md) — aquele é o contrato técnico (endpoints/JSON/proxy); este é o comportamento da tela em si.

## O que a tela faz

Um único campo de entrada: o usuário cola o editorial completo (texto puro, sem formatação obrigatória) e aciona a geração. Todo o resto — layout, tema de cor, imagens, diagramas, montagem do PDF — acontece no backend, sem escolha manual nesta v1.

## Fluxo, passo a passo

1. Usuário cola o editorial no campo de texto (textarea grande, tipo chat). Botão "Gerar slides" desabilitado enquanto vazio.
2. Usuário aciona a geração (Enter ou clique). Chama `POST /decks` via proxy do backend do frontend — responde em <1s, é só a criação do job.
3. Campo trava, tela entra em "Processando". O editorial enviado continua visível, mas não editável.
4. Tela consulta `GET /decks/{id}` a cada 3–5s.
5. Etapas avançam: **structure → assets → render → qa** — 4 estágios discretos, não percentual contínuo.
6. Concluído: aparecem duas opções de download — **PDF** e **PPTX** (PowerPoint editável). Se o QA encontrou algo, mostra aviso não bloqueante (deck continua baixável nos dois formatos).
7. Usuário baixa no formato que quiser (`GET /decks/{id}/download?format=pdf` ou `?format=pptx` — os dois já estão prontos, não precisa esperar de novo) ou clica "Gerar outro" (limpa o campo).

**Sobre a escolha de formato**: os dois arquivos são gerados juntos, sempre — não existe pergunta "PDF ou PPTX?" antes de começar a gerar. A escolha é só na hora de baixar (dois botões, ou um menu "Baixar como ▾"), porque gerar os dois não tem custo extra (nenhum dos dois passa por IA).

## As quatro etapas (rótulos sugeridos pro usuário final)

| Etapa técnica | Rótulo sugerido | O que faz |
|---|---|---|
| `structure` | Estruturando conteúdo | Lê o editorial e decide slides, layout e conteúdo de cada um |
| `assets` | Gerando imagens e diagramas | Gera as imagens/diagramas que o editorial pediu |
| `render` | Montando PDF | Monta o PDF final, 16:9 |
| `qa` | Verificando qualidade | Confere texto cortado, contraste de cor, imagens que falharam |

## Estados da tela

- **Vazio** — campo em branco, botão desabilitado.
- **Enviando** — entre o clique e a resposta do `POST /decks`.
- **Processando** — stepper ativo (uma das 4 etapas em destaque), campo bloqueado, sem botão de cancelar nesta v1.
- **Concluído, sem avisos** — botões "Baixar PDF" e "Baixar PPTX" (os dois já prontos) + "Gerar outro".
- **Concluído, com avisos** — mesmos dois botões de download + aviso expansível ("N pontos de atenção neste deck", lista de `issues` do QA). Nunca bloqueia.
- **Falhou** — mensagem baseada no `error` da etapa que falhou + botão "Tentar novamente" (reenvia o mesmo editorial).

## Dados de entrada/saída (resumo — JSON completo em HANDOFF_FRONTEND.md)

| | |
|---|---|
| entra | `editorial_text` — texto puro colado pelo usuário |
| sai | PDF **e** PPTX (os dois sempre gerados), cada um via URL assinada (1h) + `issues[]`/`issue_count` opcional do QA |
| polling | a cada 3–5s até `status` virar `completed` ou `failed` |
| auth | sessão normal do app (ver pendência abaixo) |

## Fora de escopo nesta v1

- Preview slide a slide antes do PDF pronto.
- Histórico de decks anteriores (só consulta por id conhecido).
- Edição do deck já gerado (o caminho é ajustar o editorial e gerar de novo).
- Cancelar geração em andamento.

## Pendências antes de implementar

1. **Decisão de produto em aberto**: tela atrás de sessão normal (qualquer usuário logado) ou restrita a administrador? Suposição usada aqui: sessão normal — confirmar antes de codar a proteção de rota.
2. **Configuração de ambiente**: URL pública do serviço + API key de servidor-a-servidor precisam vir do time de backend antes de apontar o proxy pra qualquer ambiente além do de teste.
