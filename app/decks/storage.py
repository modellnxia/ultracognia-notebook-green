"""
Cliente do Supabase Storage — via httpx puro contra a API REST, sem trazer
o SDK `supabase-py` como dependência nova (Storage é só uma API HTTP simples;
não precisa de um cliente dedicado pra isso, e manter esse módulo só com
libs que o resto do projeto já usa ajuda na portabilidade — ver README.md).

Credenciais lidas direto de env var, não do `Settings` global do host — pra
esse módulo não precisar que o host declare nada especial sobre ele (ponto
de acoplamento isolado, documentado no README.md desta pasta).
"""

import os

import httpx

_BUCKET = "decks"


def _base_url() -> str:
    url = os.getenv("SUPABASE_URL")
    if not url:
        raise RuntimeError("SUPABASE_URL não configurada.")
    return url.rstrip("/")


def _service_role_key() -> str:
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY não configurada.")
    return key


def _headers() -> dict[str, str]:
    key = _service_role_key()
    return {"Authorization": f"Bearer {key}", "apikey": key}


async def ensure_bucket_exists(bucket: str = _BUCKET, *, public: bool = False) -> None:
    """Cria o bucket se ainda não existir. Idempotente — 'já existe' não é erro."""
    async with httpx.AsyncClient(base_url=_base_url(), headers=_headers(), timeout=30) as client:
        resp = await client.post(
            "/storage/v1/bucket", json={"id": bucket, "name": bucket, "public": public}
        )
        if resp.status_code == 200:
            return
        # Supabase responde 400 com essa mensagem quando o bucket já existe.
        if resp.status_code == 400 and "already exists" in resp.text.lower():
            return
        resp.raise_for_status()


async def upload_object(
    path: str, content: bytes, content_type: str, *, bucket: str = _BUCKET
) -> None:
    """Sobe (ou sobrescreve, com upsert) um objeto no bucket."""
    async with httpx.AsyncClient(base_url=_base_url(), headers=_headers(), timeout=60) as client:
        resp = await client.post(
            f"/storage/v1/object/{bucket}/{path}",
            content=content,
            headers={"Content-Type": content_type, "x-upsert": "true"},
        )
        resp.raise_for_status()


async def create_signed_url(path: str, *, bucket: str = _BUCKET, expires_in: int = 3600) -> str:
    """Gera uma URL assinada válida por `expires_in` segundos pra baixar o objeto."""
    async with httpx.AsyncClient(base_url=_base_url(), headers=_headers(), timeout=30) as client:
        resp = await client.post(
            f"/storage/v1/object/sign/{bucket}/{path}",
            json={"expiresIn": expires_in},
        )
        resp.raise_for_status()
        signed_path = resp.json()["signedURL"]
        return f"{_base_url()}/storage/v1{signed_path}"
