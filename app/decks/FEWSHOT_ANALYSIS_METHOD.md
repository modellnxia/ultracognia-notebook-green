# Técnica de análise forense de few-shots visuais

> Como extrair, de um PDF de referência (deck real gerado no NotebookLM/Gemini),
> uma descrição precisa o suficiente pra virar restrição de prompt/CSS — e não
> um chute estético. Escrito em 2026-08-20 depois de aplicar a técnica pela
> primeira vez (ver [`FEWSHOT_CATALOG_SHOT2.md`](FEWSHOT_CATALOG_SHOT2.md) slide
> 1) e formalizado antes de repetir pros outros 58 exemplos. Objetivo: não
> esquecer o processo entre sessões.

## Por que isso existe

A primeira tentativa de descrever um slide de referência, olhando só a miniatura
que já estava vendorizada em `app/decks/static/fewshot/`, saiu genérica demais
("fundo azul escuro, painéis com bordas arredondadas") — informação real
insuficiente pra virar regra de CSS ou constraint de prompt. A miniatura
vendorizada é uma imagem comprimida (1100px, JPEG q78) pensada pra ir num
payload de API, não pra leitura forense de detalhe (raio de borda exato,
espessura de linha, posição de texto em pixel).

A técnica abaixo resolve isso: sempre voltar ao PDF original e renderizar em
resolução muito acima do necessário pra visualização normal.

## Passo 1 — Render em alta resolução, nunca a miniatura

```python
import pymupdf
doc = pymupdf.open(r"caminho\para\shotN.pdf")
page = doc[indice_da_pagina]
mat = pymupdf.Matrix(zoom, zoom)   # zoom 4-6x já resolve bem
pix = page.get_pixmap(matrix=mat)
pix.save("pagina_hires.png")
```

Regra prática: `zoom=3` já é legível pra composição geral; `zoom=6` é o que
permite ler texto pequeno dentro de painel e perceber espessura de borda de
1-2px. Sempre que uma pergunta for sobre detalhe fino (borda, cor exata,
posição), re-renderizar mais a página inteira em `zoom=6`, nunca tentar
"zoom digital" numa imagem já pequena — perde informação que não existe mais.

## Passo 2 — Se a página é um "contact sheet" (vários slides numa página), recortar cada um antes de analisar

Páginas de contact sheet (`shot2.pdf`, `shot3.pdf`) têm 1-9 slides por página
numa grade. Analisar a grade inteira de uma vez mistura conteúdo de slides
diferentes na descrição. Sempre recortar em arquivos individuais primeiro.

**Achado real, documentado pra não repetir o erro**: dividir a largura da
página em N colunas iguais (N = número de cards que parecem estar na linha)
**falha silenciosamente** quando os cards não têm a mesma largura — um card
com ilustração maior pode ocupar quase o dobro do espaço de um vizinho. Cortar
por N-avos nesse caso corta o card largo ao meio.

Também **não confiar em detecção automática de "gutter" por brilho/branco**:
tentei (`col_mean > limiar`) e falhou de duas formas diferentes:
- Limiar alto (245, branco puro): nunca dispara — a sombra/glow que os cards
  projetam pro fundo branco nunca deixa a média da coluna chegar perto disso.
- Limiar baixo (120-200): dispara demais — pega brilho interno do próprio
  card (texto branco, brilho de ilustração) como se fosse gutter, fatiando um
  card só em vários pedaços.

**O que funcionou**: bandas de **linha** inteira (`row_mean` ao longo da
largura toda) são confiáveis — o espaço entre uma linha e outra da grade É
branco de ponta a ponta, sem esse problema de vazamento. Usar isso pra achar
as linhas, e dentro de cada linha:
- Se os cards daquela linha parecem visualmente do mesmo tamanho → dividir em
  N colunas iguais (rápido, funcionou bem pra grades 3×3 regulares).
- Se algum card parece maior/menor → **medir manualmente com régua** (passo 3),
  não confiar em heurística automática.

```python
gray = np.array(img.convert("RGB")).mean(axis=2)
row_white = gray.mean(axis=1) > 245        # linha inteira, confiável
row_bands = find_content_bands(row_white)  # (y0,y1) de cada linha da grade
```

## Passo 3 — Régua sobreposta pra achar fronteira exata (só quando necessário)

Pra quando o passo 2 não é confiável (card de largura diferente), sobrepor uma
grade de linhas verticais com o valor do pixel escrito a cada 100px, exportar,
e ler a fronteira real a olho:

```python
from PIL import ImageDraw
draw = ImageDraw.Draw(img)
for x in range(0, img.width, 100):
    draw.line([(x, 0), (x, img.height)], fill=(255, 180, 0), width=1)
    draw.text((x + 3, 3), str(x), fill=(255, 0, 0))
```

Se a imagem for larga demais pra caber legível numa visualização só, split em
metade esquerda/direita com uma margem de sobreposição (~100-200px) antes de
exportar, pra não perder a fronteira bem no meio do corte.

**Sempre validar o corte depois de cortar** — abrir o resultado e conferir que
nenhum texto/painel ficou cortado na borda. Na primeira tentativa em
`shot3_p03`, o corte cortou a palavra "INTELLIGENCE" pela metade — só apareceu
ao abrir o resultado, não dava pra prever só olhando a régua rapidamente.
Ajustar e recortar de novo é normal, faz parte do processo, não pular essa
verificação.

## Passo 4 — A leitura forense em si (o que realmente vai na descrição)

Com o crop individual e limpo em mãos, ler em camadas, nessa ordem:

1. **Composição geral primeiro**: proporção aproximada de área ilustração vs.
   área texto/painéis (ex.: "~30% esquerda ilustração, ~70% direita texto"),
   número de zonas horizontais/verticais.
2. **Cabeçalho/eyebrow**: existe badge de categoria? Formato (pílula/retângulo
   com canto arredondado)? Cor de fundo vs. cor do texto? Case (tudo
   maiúsculo)? Posição exata (canto superior, alinhado com quê)?
3. **Título/subtítulo**: peso da fonte (bold/black), tamanho relativo ao resto,
   quantas linhas, cor (branco puro? Com um tom de destaque em parte do
   texto?), se tem palavra com cor de destaque no meio da frase.
4. **Ilustração**: estilo (foto-realista/render 3D isométrico/linha), paleta
   dominante, se tem watermark (qual texto exato, posição).
5. **Painéis/cards internos**: quantos, raio de borda (canto bem arredondado
   vs. levemente arredondado — comparar visualmente com referência conhecida,
   tipo "raio ~8-12px nesse zoom"), cor de fundo (mais clara ou mais escura que
   o fundo do slide?), borda (tem stroke visível? De que cor/espessura?), como
   o texto se organiza dentro (label em cima, valor embaixo, tudo alinhado à
   esquerda?).
6. **Citação/rodapé**: barra inteira? Cor de fundo diferente do resto?
   Trecho em negrito dentro da citação (citando autor/framework)?
7. **Assinatura/watermark do rodapé**: texto exato, posição, opacidade
   aparente.

Sempre citar **posição em termos relativos claros** (não "em algum lugar à
direita", e sim "no terço superior direito, alinhado à borda direita do
painel") e **cor por nome + intensidade aproximada**, não código hex chutado
(a não ser que dê pra amostrar o pixel de verdade).

## Quando repetir tudo isso pra um lote grande (ex.: os 59 few-shots)

1. Recortar tudo primeiro (passos 1-3), validar visualmente cada corte antes
   de escrever qualquer descrição — misturar corte errado com análise forense
   desperdiça a análise.
2. Description por slide, uma de cada vez, seguindo o passo 4 — não
   generalizar "esse é igual ao anterior" sem checar; dois slides da mesma
   família visual costumam ter pequenas diferenças reais (contagem de
   painéis, presença ou não de citação) que importam pra virar constraint.
3. Consolidar num catálogo por fonte (ver `FEWSHOT_CATALOG_SHOT1.md`,
   `_SHOT2.md`, `_SHOT3.md`) em vez de um arquivo por slide — mais fácil de
   ler em sequência e comparar padrões entre os exemplos da mesma família.
