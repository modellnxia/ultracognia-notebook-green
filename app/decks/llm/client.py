"""
Cliente de LLM fino — a única porta de entrada pra gerar texto por IA nesse
módulo. Lê os providers ativos (ver providers.py) e tenta cada um em ordem
de prioridade, pulando quem não tem chave de API configurada; só levanta
erro se todos falharem (ou nenhum tiver chave).

Dois formatos de chamada: Gemini tem formato próprio (`generateContent`);
DeepSeek, OpenRouter e OpenAI são OpenAI-compatible (`/chat/completions`) —
o mesmo adaptador serve os três (OpenAI é literalmente o formato original
que os outros dois imitam).

Suporte a imagem de referência (few-shot visual, 2026-08-19 — ver
structure.py): Gemini sempre teve (nativo). OpenAI ganhou em 2026-08-21 —
model vision-capable de verdade (`gpt-5.4`, validado manualmente com uma
imagem real antes de entrar em produção), formato `image_url` com data URI
base64 dentro do array `content` da mensagem. DeepSeek/OpenRouter continuam
sem — não são multimodais, as imagens são simplesmente ignoradas (loga
aviso) se pedidas pra eles.
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
# fallback.
_API_KEY_ENV_BY_PROVIDER = {
    "gemini": "GEMINI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
}

# DeepSeek/OpenRouter/OpenAI exigem um "model" explícito no corpo da
# requisição — diferente do Gemini, que já embute o modelo na própria URL
# cadastrada. `gpt-5.4` validado manualmente (chamada real, texto e
# multimodal) em 2026-08-21 antes de virar default.
_OPENAI_COMPAT_DEFAULT_MODEL = {
    "deepseek": "deepseek-chat",
    "openrouter": "google/gemini-2.5-flash",
    "openai": "gpt-5.4",
}

# Só o OpenAI, entre os três providers OpenAI-compatible, é vision-capable
# de verdade (validado manualmente, ver docstring do módulo) — DeepSeek/
# OpenRouter recebem só o texto do prompt mesmo se `reference_images` vier
# preenchido (a descrição em texto do padrão visual já cobre esse caso, ver
# `_FEWSHOT_DESCRIPTION` em structure.py).
_VISION_CAPABLE_OPENAI_COMPAT_PROVIDERS = {"openai"}


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
    url: str,
    api_key: str,
    model: str,
    prompt: str,
    reference_images: list[tuple[bytes, str]] | None = None,
) -> tuple[str, int, int]:
    """
    `reference_images` só deve ser passado por quem chama quando o provider
    é confirmadamente vision-capable (ver `_VISION_CAPABLE_OPENAI_COMPAT_PROVIDERS`
    em `generate_text`) — aqui a função só monta o formato, não decide se
    faz sentido mandar imagem pra esse provider.
    """
    if reference_images:
        content: list[dict] = [{"type": "text", "text": prompt}]
        for image_bytes, mime_type in reference_images:
            b64 = base64.b64encode(image_bytes).decode()
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}})
    else:
        content = prompt

    async with httpx.AsyncClient(timeout=90) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "messages": [{"role": "user", "content": content}]},
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
      - `preferred_provider="gemini"/"deepseek"/"openai"` (combo de escolha na tela —
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

            model = _OPENAI_COMPAT_DEFAULT_MODEL.get(name, "auto")
            if reference_images and name not in _VISION_CAPABLE_OPENAI_COMPAT_PROVIDERS:
                logger.warning(
                    "Provider '%s' não é vision-capable — chamando só com texto.", name,
                )
                return await _call_openai_compatible(provider["url"], api_key, model, prompt)
            return await _call_openai_compatible(provider["url"], api_key, model, prompt, reference_images)
        except Exception as exc:
            logger.warning("Provider '%s' falhou, tentando próximo: %s", name, exc)
            last_error = exc

    if not tried_any:
        raise LLMError("Nenhum provider de LLM tem chave de API configurada.")
    raise LLMError(f"Todos os providers de LLM falharam. Último erro: {last_error}")
