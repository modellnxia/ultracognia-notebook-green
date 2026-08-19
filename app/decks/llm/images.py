"""
Cliente de geração de imagem. Dois providers, escolhidos por
`DECK_IMAGE_PROVIDER` (default `gemini`):

  - `gemini`: Gemini nativo ("Nano Banana", decisão de 2026-08-17: sem
    Midjourney/Figma/Gamma, ver README). Provider de produção — exige
    billing habilitado no projeto do Google Cloud por trás da chave (tier
    gratuito tem cota 0 pra esse modelo, descoberto em 2026-08-18).
  - `pollinations`: Pollinations.ai, gratuito, sem chave/cadastro nenhum
    (GET simples). Usado só como alternativa de POC/teste enquanto o
    billing do Gemini não é resolvido — SEM SLA, sem garantia de uptime,
    moderação de conteúdo mais solta. Não é o provider pensado pra produção
    com o cliente final, só pra desbloquear validação ponta a ponta.

Fora do fallback multi-provider de `client.py` de propósito: hoje nenhum
dos dois vem da tabela `providers` (essa tabela é só de texto/chat) —
quando isso mudar, este módulo se junta a `generate_text()`.
"""

import base64
import logging
import os
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-image"
_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
_POLLINATIONS_BASE_URL = "https://image.pollinations.ai/prompt"


class ImageGenerationError(RuntimeError):
    """A geração de imagem falhou (chave ausente, API retornou erro, ou resposta sem imagem)."""


async def generate_image(prompt: str) -> tuple[bytes, str]:
    """
    Gera uma imagem a partir de um prompt de texto. Retorna
    (bytes_da_imagem, mime_type). Levanta `ImageGenerationError` se algo
    der errado — quem chama (`steps.py::_resolve_slide_asset`) já trata
    isso com isolamento de falha por asset, não precisa tratar aqui.
    """
    provider = os.getenv("DECK_IMAGE_PROVIDER", "gemini").strip().lower()
    if provider == "pollinations":
        return await _generate_image_pollinations(prompt)
    if provider == "gemini":
        return await _generate_image_gemini(prompt)
    raise ImageGenerationError(
        f"DECK_IMAGE_PROVIDER desconhecido: {provider!r} (use 'gemini' ou 'pollinations')."
    )


async def _generate_image_gemini(prompt: str) -> tuple[bytes, str]:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ImageGenerationError("GEMINI_API_KEY não configurada.")

    model = os.getenv("GEMINI_IMAGE_MODEL", _DEFAULT_IMAGE_MODEL)
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


async def _generate_image_pollinations(prompt: str) -> tuple[bytes, str]:
    """Sem chave, sem cadastro — só um GET. Ver aviso de uso no docstring do módulo."""
    url = f"{_POLLINATIONS_BASE_URL}/{quote(prompt)}"
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.get(
            url, params={"width": 1024, "height": 576, "nologo": "true"}
        )
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "image/jpeg").split(";")[0]
        if not content_type.startswith("image/"):
            raise ImageGenerationError(
                f"Pollinations não devolveu uma imagem (content-type={content_type!r})."
            )
        return resp.content, content_type
