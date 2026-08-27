"""
Cliente de geração de imagem. Três providers, escolhidos por
`DECK_IMAGE_PROVIDER` (default `gemini`):

  - `gemini`: Gemini nativo ("Nano Banana", decisão de 2026-08-17: sem
    Midjourney/Figma/Gamma, ver README). Exige billing habilitado no
    projeto do Google Cloud por trás da chave (tier gratuito tem cota 0 pra
    esse modelo, descoberto em 2026-08-18) — ainda não resolvido em
    2026-08-21, fica indisponível até isso acontecer.
  - `openai`: API de imagens da OpenAI (`gpt-image-1`, endpoint
    `/v1/images/generations` — não é o ChatGPT, é a API paga separada,
    exige `OPENAI_API_KEY` com billing habilitado no projeto). Provider
    ativo em produção desde 2026-08-21, substituindo o Pollinations (ver
    histórico — Pollinations foi removido de propósito: sem SLA, moderação
    solta, só servia de POC enquanto nenhum dos dois providers pagos tinha
    billing resolvido).
  - `openrouter`: endpoint dedicado de imagem do OpenRouter
    (`/api/v1/images`, distinto do chat/completions — achado real
    verificando a doc antes de implementar, 2026-08-27), roteando pra
    vários modelos (default `google/gemini-2.5-flash-image`, validado
    manualmente com chave real: texto, imagem E visão/multimodal, os três
    funcionando). Reativa o OpenRouter no combo de providers (removido do
    combo em 2026-08-21 por só ter texto na época — agora tem paridade com
    os outros 3: texto + imagem no mesmo provider).

Fora do fallback multi-provider de `client.py` de propósito: hoje nenhum
dos três vem da tabela `providers` (essa tabela é só de texto/chat) —
quando isso mudar, este módulo se junta a `generate_text()`.
"""

import base64
import logging
import os

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_GEMINI_IMAGE_MODEL = "gemini-3.1-flash-image"
_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

_DEFAULT_OPENAI_IMAGE_MODEL = "gpt-image-1"
_DEFAULT_OPENAI_IMAGE_QUALITY = "high"  # a mais alta disponível — decisão explícita do usuário em 2026-08-21
_DEFAULT_OPENAI_IMAGE_SIZE = "1024x1024"
_OPENAI_IMAGES_URL = "https://api.openai.com/v1/images/generations"

_DEFAULT_OPENROUTER_IMAGE_MODEL = "google/gemini-2.5-flash-image"
_OPENROUTER_IMAGES_URL = "https://openrouter.ai/api/v1/images"


class ImageGenerationError(RuntimeError):
    """A geração de imagem falhou (chave ausente, API retornou erro, ou resposta sem imagem)."""


async def generate_image(prompt: str, *, provider: str | None = None) -> tuple[bytes, str]:
    """
    Gera uma imagem a partir de um prompt de texto. Retorna
    (bytes_da_imagem, mime_type). Levanta `ImageGenerationError` se algo
    der errado — quem chama (`steps.py::_resolve_slide_asset`) já trata
    isso com isolamento de falha por asset, não precisa tratar aqui.

    `provider`: override explícito (2026-08-21 — combo de opções na tela,
    cada uma travada ponta a ponta: `llm_provider="gemini"` usa Gemini pra
    tudo, `llm_provider="deepseek"` usa OpenAI pra imagem (DeepSeek não tem
    produto de imagem), `llm_provider="openai"`/`"openrouter"` usam o
    próprio provider pra imagem também — ver `steps.py::run_assets`). `None`
    (padrão) mantém o comportamento antigo, lendo `DECK_IMAGE_PROVIDER` da
    env var — usado quando o job não tem `llm_provider` definido (fallback
    automático, sem escolha explícita).
    """
    provider = (provider or os.getenv("DECK_IMAGE_PROVIDER", "gemini")).strip().lower()
    if provider == "openai":
        return await _generate_image_openai(prompt)
    if provider == "gemini":
        return await _generate_image_gemini(prompt)
    if provider == "openrouter":
        return await _generate_image_openrouter(prompt)
    raise ImageGenerationError(
        f"DECK_IMAGE_PROVIDER desconhecido: {provider!r} (use 'gemini', 'openai' ou 'openrouter')."
    )


async def _generate_image_gemini(prompt: str) -> tuple[bytes, str]:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ImageGenerationError("GEMINI_API_KEY não configurada.")

    model = os.getenv("GEMINI_IMAGE_MODEL", _DEFAULT_GEMINI_IMAGE_MODEL)
    url = f"{_GEMINI_BASE_URL}/{model}:generateContent"

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            url,
            params={"key": api_key},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
            },
        )
        resp.raise_for_status()
        data = resp.json()

    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    for part in parts:
        inline = part.get("inlineData")
        if inline and inline.get("data"):
            return base64.b64decode(inline["data"]), inline.get("mimeType", "image/png")

    raise ImageGenerationError(f"Gemini não retornou nenhuma imagem na resposta: {data}")


async def _generate_image_openai(prompt: str) -> tuple[bytes, str]:
    """
    API de imagens da OpenAI (`gpt-image-1`) — sempre devolve base64
    (`b64_json`), nunca URL, então não precisa de um segundo download como
    o Gemini às vezes exige. Qualidade/tamanho configuráveis por env var,
    default = qualidade máxima (`high`) — validado manualmente com uma
    chave real em 2026-08-21 antes de entrar em produção (mesma disciplina
    de sempre: nunca construir em cima de chave não testada).
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ImageGenerationError("OPENAI_API_KEY não configurada.")

    model = os.getenv("OPENAI_IMAGE_MODEL", _DEFAULT_OPENAI_IMAGE_MODEL)
    quality = os.getenv("OPENAI_IMAGE_QUALITY", _DEFAULT_OPENAI_IMAGE_QUALITY)
    size = os.getenv("OPENAI_IMAGE_SIZE", _DEFAULT_OPENAI_IMAGE_SIZE)

    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post(
            _OPENAI_IMAGES_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "prompt": prompt, "size": size, "quality": quality, "n": 1},
        )
        resp.raise_for_status()
        data = resp.json()

    items = data.get("data", [])
    if items and items[0].get("b64_json"):
        image_bytes = base64.b64decode(items[0]["b64_json"])
        output_format = data.get("output_format", "png")
        return image_bytes, f"image/{output_format}"

    raise ImageGenerationError(f"OpenAI não retornou nenhuma imagem na resposta: {data}")


async def _generate_image_openrouter(prompt: str) -> tuple[bytes, str]:
    """
    Endpoint dedicado de imagem do OpenRouter (`/api/v1/images`, distinto do
    `/chat/completions` já usado por `client.py` pra texto) — sempre devolve
    base64 (`b64_json`), mesmo formato de resposta do provider OpenAI, mas
    com `media_type` no lugar de `output_format`. Validado manualmente com
    chave real em 2026-08-27 antes de entrar em produção (mesma disciplina
    de sempre).
    """
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ImageGenerationError("OPENROUTER_API_KEY não configurada.")

    model = os.getenv("OPENROUTER_IMAGE_MODEL", _DEFAULT_OPENROUTER_IMAGE_MODEL)

    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post(
            _OPENROUTER_IMAGES_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "prompt": prompt, "n": 1},
        )
        resp.raise_for_status()
        data = resp.json()

    items = data.get("data", [])
    if items and items[0].get("b64_json"):
        image_bytes = base64.b64decode(items[0]["b64_json"])
        media_type = items[0].get("media_type", "image/png")
        return image_bytes, media_type

    raise ImageGenerationError(f"OpenRouter não retornou nenhuma imagem na resposta: {data}")
