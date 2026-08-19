"""
Cliente de LLM fino — a única porta de entrada pra gerar texto por IA nesse
módulo. Lê os providers ativos (ver providers.py) e tenta cada um em ordem
de prioridade, pulando quem não tem chave de API configurada; só levanta
erro se todos falharem (ou nenhum tiver chave).

Dois formatos de chamada, não três: Gemini tem formato próprio
(`generateContent`); DeepSeek e OpenRouter são OpenAI-compatible
(`/chat/completions`) — o mesmo adaptador serve os dois.
"""

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


async def _call_gemini(url: str, api_key: str, prompt: str) -> tuple[str, int, int]:
    async with httpx.AsyncClient(timeout=90) as client:
        resp = await client.post(
            url,
            params={"key": api_key},
            json={"contents": [{"parts": [{"text": prompt}]}]},
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


async def generate_text(prompt: str, conn: asyncpg.Connection) -> tuple[str, int, int]:
    """
    Gera texto tentando cada provider ativo em ordem de prioridade.
    Retorna (texto, tokens_entrada, tokens_saida). Levanta LLMError só se
    todos os providers com chave configurada falharem.
    """
    providers = await list_active_providers(conn)
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
                return await _call_gemini(provider["url"], api_key, prompt)
            model = _OPENAI_COMPAT_DEFAULT_MODEL.get(name, "auto")
            return await _call_openai_compatible(provider["url"], api_key, model, prompt)
        except Exception as exc:
            logger.warning("Provider '%s' falhou, tentando próximo: %s", name, exc)
            last_error = exc

    if not tried_any:
        raise LLMError("Nenhum provider de LLM tem chave de API configurada.")
    raise LLMError(f"Todos os providers de LLM falharam. Último erro: {last_error}")
