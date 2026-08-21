# Rulebook compacto — guardrails de composição

> Destilado dos 59 exemplos catalogados em `FEWSHOT_CATALOG_SHOT{1,2,3}.md`
> (ver [`FEWSHOT_ANALYSIS_METHOD.md`](FEWSHOT_ANALYSIS_METHOD.md) pra técnica).
> Isto é o que entra no prompt do `structure.py` — os catálogos completos
> ficam como documentação de referência, não vão pro prompt (custo de token,
> muita redundância entre slides da mesma família).

## Dois níveis de guardrail

**Estrutural (hard, sem exceção — vira CSS fixo, a IA nem escolhe)**: raio de
borda, espessura de linha, formato geométrico de cada peça abaixo. Não
depende de conteúdo, não faz sentido a IA "decidir" isso por slide.

**Criativo (forte default, com margem — vira campo no schema, restrito a um
conjunto fechado de opções)**: qual peça usar em cada slide, tom de cor
dentro da paleta escura, presença de ilustração. A IA escolhe dentro do
conjunto fechado, guiada pelo que o editorial pede — nunca inventa opção
fora da lista, mas também não é forçada a repetir sempre a mesma.

## Peças (vocabulário fechado)

### Posição da ilustração
`left` (padrão mais comum, ~45% da largura) · `right` (inverte, visto em
vários slides de `shot3`) · `background` (full-bleed atrás de tudo — usado
nos slides de fechamento/transição) · `none` (raro nos 59, mas existe —
slides muito densos em texto podem prescindir).

### Estilo de eyebrow
`chamfered-left` (moldura vazada, cantos chanfrados, dourado — família
`shot2`) · `pill-left` (pill arredondado, ciano, sem separador — família
`shot3`) · `pill-center` (centralizado, glow forte — reservado pra slides de
fechamento) · `none`.

### Estilo de painel
`void-frame` (moldura vazada sem preenchimento, cantos chanfrados) ·
`bordered-card` (fundo escuro translúcido + borda fina colorida, cantos
arredondados ~8-10px — o mais comum nos 59) · `borderless-icon` (sem
moldura nenhuma, só ícone de linha + label + texto).

### Arranjo de conteúdo
`stacked` (painéis empilhados verticalmente) · `row` (fileira única
horizontal) · `comparison-columns` (2 colunas lado a lado com divisor,
pra antes/depois ou A vs. B) · `sequence-numbered` (itens conectados em
sequência, marcador numérico 1-2-3...) · `sequence-lettered` (mesmo
conceito, marcador A-B-C). *(Arranjos mais complexos do corpus —
`flow-arrows`, `table`, `radial`, `hierarchy` — documentados mas **não**
implementados nesta fatia; ver pendência no fim deste arquivo.)*

### Estilo de citação
`bar-circuit-corners` (barra larga, cantos com traço em L) ·
`bordered-card` (mesmo peso visual dos painéis de conteúdo) · `split-two`
(2 caixas lado a lado, uma frase-resumo + a citação formal).

### Cor de fundo — paleta escura, validada, não fixa
A IA continua escolhendo `theme.palette.background` livremente dentro do
espírito do editorial — **mas o valor é validado por luminância** (ver
`models.py`): qualquer hex com luminância relativa ≥ 0.25 é rejeitado
(reentra no loop de correção já existente em `structure.py`, mesmo
mecanismo usado hoje pra erro de schema). Isso cobre navy, petróleo, preto-
azulado, verde-escuro, bordô-escuro, cinza-grafite — a faixa real observada
nos 59 — e bloqueia estruturalmente qualquer tom claro/branco, fechando o
bug de fundo quase-branco relatado em produção.

### Ilustração — forte default, não trava de schema
Nos 59 exemplos, **todos** têm algum asset visual — isso é evidência real a
favor de manter ilustração como comportamento padrão. Mas não é regra
técnica obrigatória: o prompt orienta fortemente pra incluir, sem impedir a
IA de decidir que um slide específico dispensa (schema já trata `asset`
como opcional).

## Pendência — arranjos ainda não implementados

`flow-arrows` (setas conectando nós em fluxo, ex.: slide 3/`shot1_p07`),
`table` (grade linhas×colunas, ex.: slide 5/`shot1_p05`), `radial` (elementos
girando em torno de um núcleo central, ex.: slide 5/6/8 de `shot2_p01`),
`hierarchy` (pirâmide/organograma, ex.: slide 12/`shot2_p02`) — todos
documentados no rulebook e nos catálogos, mas exigem mais trabalho de
CSS/posicionamento (setas, geometria não-retangular) do que dá pra entregar
na entrega imediata pedida. Próxima fatia natural depois de validar que o
resto está funcionando.
