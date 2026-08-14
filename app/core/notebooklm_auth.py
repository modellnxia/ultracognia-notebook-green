"""
Bootstrap do master token do NotebookLM em ambientes efêmeros (Railway).

A lib notebooklm-py sabe ler o storage_state.json direto de uma env var
(NOTEBOOKLM_AUTH_JSON), mas o master_token.json não tem esse atalho — só é
aceito como arquivo físico em ~/.notebooklm/profiles/<profile>/master_token.json
(ver docs da lib, seção "headless server or CI"). Como o Railway recria o
container do zero a cada deploy (sem disco persistente), esse módulo
materializa o arquivo a partir da env var NOTEBOOKLM_MASTER_TOKEN_JSON antes
da aplicação subir — precisa rodar em todo boot, não só uma vez.

Sem efeito nenhum se a variável não estiver setada — mantém compatibilidade
com ambientes que ainda usam só storage_state.json/NOTEBOOKLM_AUTH_JSON
(ex.: o environment `modellnx_blue`, enquanto não migrado pra esse modo).
"""

import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_PROFILE_DIR = Path.home() / ".notebooklm" / "profiles" / "default"
_MASTER_TOKEN_PATH = _PROFILE_DIR / "master_token.json"
_STORAGE_STATE_PATH = _PROFILE_DIR / "storage_state.json"


def _notebooklm_executable() -> str:
    """Resolve o notebooklm no mesmo ambiente Python rodando este processo."""
    candidate = Path(sys.executable).parent / (
        "notebooklm.exe" if sys.platform == "win32" else "notebooklm"
    )
    return str(candidate) if candidate.exists() else "notebooklm"


def bootstrap_master_token() -> None:
    """Prepara a autenticação master-token no boot, se configurada.

    Dois passos, ambos sem efeito se NOTEBOOKLM_MASTER_TOKEN_JSON não estiver
    setada (mantém compatibilidade com ambientes que ainda usam só
    storage_state.json/NOTEBOOKLM_AUTH_JSON, ex.: modellnx_blue):

      1. Materializa master_token.json no disco a partir da env var — a lib
         só aceita o master token como arquivo físico, não direto de env var.
      2. Pré-gera um storage_state.json válido rodando
         `notebooklm login --master-token-refresh` uma vez, no boot. Sem
         isso, um container novo (sem esse arquivo ainda) cairia num erro
         diferente (arquivo inexistente) que NOTEBOOKLM_REFRESH_CMD não
         sabe recuperar sozinho — esse mecanismo só reage a sessão
         *expirada* (arquivo existe mas é inválido), não a arquivo ausente.
    """
    master_token_json = os.getenv("NOTEBOOKLM_MASTER_TOKEN_JSON")
    if not master_token_json:
        logger.debug(
            "NOTEBOOKLM_MASTER_TOKEN_JSON não definida — pulando bootstrap do master token "
            "(ambiente ainda usa só storage_state.json/NOTEBOOKLM_AUTH_JSON)."
        )
        return

    _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    _MASTER_TOKEN_PATH.write_text(master_token_json, encoding="utf-8")
    try:
        os.chmod(_MASTER_TOKEN_PATH, 0o600)
    except OSError:
        # Windows local não aplica bits de permissão POSIX da mesma forma —
        # sem problema, relevante só em produção (Linux/Railway).
        pass
    logger.info("master_token.json materializado em %s a partir da env var.", _MASTER_TOKEN_PATH)

    result = subprocess.run(
        [_notebooklm_executable(), "login", "--master-token-refresh"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # Não derruba o boot por isso — se falhar aqui, a primeira chamada
        # real vai falhar também e o erro fica visível nos logs da app,
        # com o mesmo diagnóstico. Só registra pra facilitar investigação.
        logger.error(
            "Falha ao pré-gerar storage_state.json a partir do master token "
            "(código %d): %s",
            result.returncode,
            result.stderr.strip(),
        )
    else:
        logger.info("storage_state.json pré-gerado com sucesso em %s.", _STORAGE_STATE_PATH)
