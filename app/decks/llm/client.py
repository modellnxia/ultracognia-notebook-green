"""
Cliente de LLM fino — a única porta de entrada pra gerar texto por IA nesse
módulo. Lê os providers ativos (ver providers.py) e tenta cada um em ordem
de prioridade, pulando quem não tem chave de API configurada; só levanta
erro se todos falharem (ou nenhum tiver chave).

Dois formatos de chamada, não três: Gemini tem formato próprio
(`generateContent`); DeepSeek e OpenRouter são OpenAI-compatible
(`/chat/completions`) — o mesmo adaptador serve os dois.

Suporte a imagem de referência (few-shot visual, 2026-08-19 — ver
structure.py): só implementado pro Gemini, que é o único provider ativo
hoje. Se o fallback cair pro DeepSeek/OpenRouter com imagens pedidas, elas
são simplesmente ignoradas (loga aviso) — não vale a pena implementar um
formato multimodal pra um provider sem chave configurada ainda.
"""

import base64
import logging
import os

import asyncpg
import httpx

from app.decks.llm.providers import list_active_providers

logger = logging.getLogger(__name__)

# Nome da env var com a chave de API de cada provider, por nome cadastrado
# na tabela `providers`. Sem a env var setada, esse provider é pulado no
# fallback — hoje só GEMINI_API_KEY existe.
_API_KEY_ENV_BY_PROVIDER = {
    "gemini": "GEMINI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

# DeepSeek/OpenRouter exigem um "model" explícito no corpo da requisição —
# diferente do Gemini, que já embute o modelo na própria URL cadastrada.
# Ainda sem chave configurada pra nenhum dos dois; defaults razoáveis a
# revisar quando as chaves reais entrarem (ver decisão de 2026-08-18).
_OPENAI_COMPAT_DEFAULT_MODEL = {
    "deepseek": "deepseek-chat",
    "openrouter": "google/gemini-2.5-flash",
}


class LLMError(RuntimeError):
    """Nenhum provider ativo (com chave configurada) conseguiu responder."""


async def _call_gemini(
    url: str,
    api_key: str,
    prompt: str,
    reference_images: list[tuple[bytes, str]] | None = None,
) -> tuple[str, int, int]:
    # Imagens vêm ANTES do texto nas `parts` — é a ordem que a documentação do
    # Gemini recomenda pra multimodal (a imagem já dá contexto antes do
    # modelo ler a instrução).
    parts = [
        {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(image_bytes).decode()}}
        for image_bytes, mime_type in (reference_images or [])
    ]
    parts.append({"text": prompt})

    async with httpx.AsyncClient(timeout=90) as client:
        resp = await client.post(
            url,
            params={"key": api_key},
            json={"contents": [{"parts": parts}]},
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        usage = data.get("usageMetadata", {})
        return text, usage.get("promptTokenCount", 0), usage.get("candidatesTokenCount", 0)


async def _call_openai_compatible(
    url: str, api_key: str, model: str, prompt: str
) -> tuple[str, int, int]:
    async with httpx.AsyncClient(timeout=90) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}]},
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return text, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)


async def generate_text(
    prompt: str,
    conn: asyncpg.Connection,
    *,
    reference_images: list[tuple[bytes, str]] | None = None,
    preferred_provider: str | None = None,
) -> tuple[str, int, int]:
    """
    Gera texto. Dois modos:
      - `preferred_provider=None` (padrão): tenta cada provider ativo em
        ordem de prioridade, cai pro próximo se um falhar — comportamento
        de sempre.
      - `preferred_provider="gemini"/"deepseek"/"openrouter"` (2026-08-20 —
        combo de escolha na tela): usa **só** esse provider, sem cair pra
        outro se falhar. Escolha explícita do usuário pra poder comparar os
        providers de verdade — cair silenciosamente pra outro destruiria a
        comparação. Levanta `LLMError` se o provider escolhido não tiver
        chave configurada ou falhar.

    Retorna (texto, tokens_entrada, tokens_saida).

    `reference_images`: lista de (bytes, mime_type) — exemplos visuais
    anexados à chamada (few-shot), só têm efeito no Gemini (ver docstring do
    módulo) — os outros providers ignoram (o texto do prompt já carrega uma
    descrição do mesmo padrão visual, ver structure.py, então não ficam sem
    nenhum guia).
    """
    providers = await list_active_providers(conn)
    if preferred_provider is not None:
        providers = [p for p in providers if p["name"] == preferred_provider]
        if not providers:
            raise LLMError(
                f"Provider '{preferred_provider}' não está cadastrado/ativo na tabela 'providers'."
            )

    last_error: Exception | None = None
    tried_any = False

    for provider in providers:
        name = provider["name"]
        env_var = _API_KEY_ENV_BY_PROVIDER.get(name)
        api_key = os.getenv(env_var) if env_var else None
        if not api_key:
            logger.debug(
                "Provider '%s' sem chave de API configurada (%s) — pulando.", name, env_var
            )
            continue

        tried_any = True
        try:
            if name == "gemini":
                return await _call_gemini(provider["url"], api_key, prompt, reference_images)
            if reference_images:
                logger.warning(
                    "Provider '%s' não suporta imagem de referência ainda — "
                    "chamando só com texto.", name,
                )
            model = _OPENAI_COMPAT_DEFAULT_MODEL.get(name, "auto")
            return await _call_openai_compatible(provider["url"], api_key, model, prompt)
        except Exception as exc:
            logger.warning("Provider '%s' falhou, tentando próximo: %s", name, exc)
            last_error = exc

    if not tried_any:
        raise LLMError("Nenhum provider de LLM tem chave de API configurada.")
    raise LLMError(f"Todos os providers de LLM falharam. Último erro: {last_error}")
