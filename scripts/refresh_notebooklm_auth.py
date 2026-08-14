"""
Automatiza a renovação da sessão do NotebookLM usada em produção (Railway).

Dois modos:

  Padrão (storage_state.json / NOTEBOOKLM_AUTH_JSON):
      Login normal. Sessão simples, mas expira rápido na prática (observado:
      poucos dias) — precisa rodar esse script de novo toda vez que expirar.

  --master-token (recomendado, NOTEBOOKLM_MASTER_TOKEN_JSON):
      Login com conta dedicada. A aplicação se auto-renova sozinha depois
      disso via NOTEBOOKLM_REFRESH_CMD (ver app/core/notebooklm_auth.py e
      README.md) — não precisa rodar esse script de novo, a menos que o
      Google revogue a credencial.
      Requer o extra `notebooklm-py[headless]` instalado (já está no
      requirements.txt) e uma conta Google DEDICADA (não pessoal) — ver
      README.md, seção Deploy, pra entender o trade-off de risco.
      ⚠️ Esse modo REMOVE a variável NOTEBOOKLM_AUTH_JSON do ambiente —
      os dois são mutuamente exclusivos nessa versão da lib.

Em ambos os casos, o passo de login continua manual/interativo — abre o
navegador, você loga, e só isso não dá pra automatizar (exigência do
próprio Google). O que este script automatiza é tudo depois disso: captura
da credencial e envio pro Railway (já dispara redeploy automático).

Uso (de dentro do venv do projeto, pra achar o `notebooklm` no PATH):
    .venv\\Scripts\\python.exe scripts\\refresh_notebooklm_auth.py
    .venv\\Scripts\\python.exe scripts\\refresh_notebooklm_auth.py --master-token --account voce@dominio.com

Pré-requisito: scripts/.railway_token contendo o Project Token do Railway
(uma linha só, sem aspas, sem quebra de linha extra). Esse arquivo é
gitignored — nunca sobe pro repositório.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

import httpx

RAILWAY_API_URL = "https://backboard.railway.com/graphql/v2"

# IDs do projeto "MODELLNX-DEV" / serviço "notebook_dev" no Railway.
# Descobertos via API em 2026-08-11 — só mudam se o serviço for recriado.
# ⚠️ Apontam pro environment modellnx_dev. NUNCA trocar pra IDs do
# modellnx_blue/production sem autorização explícita do usuário na hora
# (regra combinada em 2026-08-12 — ver CLAUDE.md).
PROJECT_ID = "0cd78ec6-01bb-4c7d-94ca-327cae729bef"
ENVIRONMENT_ID = "5c113100-8914-474e-b8a5-287c1249bcdf"
SERVICE_ID = "d391d420-7488-4ce7-841c-643e17d3f75b"  # notebook_dev

TOKEN_FILE = Path(__file__).parent / ".railway_token"
PROFILE_DIR = Path.home() / ".notebooklm" / "profiles" / "default"
STORAGE_STATE_PATH = PROFILE_DIR / "storage_state.json"
MASTER_TOKEN_PATH = PROFILE_DIR / "master_token.json"

VARIABLE_UPSERT_MUTATION = """
mutation($input: VariableUpsertInput!) {
  variableUpsert(input: $input)
}
"""

VARIABLE_DELETE_MUTATION = """
mutation($input: VariableDeleteInput!) {
  variableDelete(input: $input)
}
"""

# Comando que a lib roda sozinha (no container) pra re-mintar cookies a
# partir do master token, quando detecta sessão expirada.
MASTER_TOKEN_REFRESH_CMD = "notebooklm login --master-token-refresh"


def load_railway_token() -> str:
    if not TOKEN_FILE.exists():
        sys.exit(
            f"Token do Railway não encontrado em {TOKEN_FILE}.\n"
            "Crie esse arquivo com o Project Token (Railway → Project Settings "
            "→ Tokens), conteúdo: só o token, uma linha."
        )
    token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if not token:
        sys.exit(f"{TOKEN_FILE} está vazio.")
    return token


def notebooklm_executable() -> str:
    """Resolve o notebooklm.exe do mesmo venv que está rodando este script.

    subprocess.run(["notebooklm", ...]) depende do PATH do processo pai — se o
    script for chamado direto via .venv\\Scripts\\python.exe (sem "ativar" o
    venv no shell), o notebooklm.exe do venv não está no PATH e o Windows não
    acha o executável. Resolvendo relativo a sys.executable evita isso.
    """
    venv_bin = Path(sys.executable).parent
    candidate = venv_bin / ("notebooklm.exe" if sys.platform == "win32" else "notebooklm")
    return str(candidate) if candidate.exists() else "notebooklm"


def run_login(master_token: bool, account: str | None) -> None:
    if master_token:
        print(
            "\n== Passo 1/3: login no NotebookLM (modo master-token) ==\n"
            f"Vai abrir o navegador. Loga com a conta {account} — use a conta "
            "DEDICADA de produção, não uma pessoal. Espera a home do NotebookLM "
            "carregar, volta aqui e confirma no terminal.\n"
        )
        cmd = [notebooklm_executable(), "login", "--master-token", "--account", account]
    else:
        print(
            "\n== Passo 1/3: login no NotebookLM ==\n"
            "Vai abrir o navegador. Loga com a conta Google de produção, espera a "
            "home do NotebookLM carregar, volta aqui e confirma no terminal.\n"
        )
        cmd = [notebooklm_executable(), "login"]

    result = subprocess.run(cmd)
    if result.returncode != 0:
        sys.exit(f"`notebooklm login` terminou com código {result.returncode}. Abortando.")


def read_credential_file(path: Path) -> str:
    if not path.exists():
        sys.exit(f"Não encontrei {path} depois do login. Algo deu errado.")
    content = path.read_text(encoding="utf-8")
    json.loads(content)  # valida que é JSON de verdade antes de mandar pro Railway
    return content


def _variable_upsert(token: str, name: str, value: str, skip_deploys: bool) -> None:
    payload = {
        "query": VARIABLE_UPSERT_MUTATION,
        "variables": {
            "input": {
                "projectId": PROJECT_ID,
                "environmentId": ENVIRONMENT_ID,
                "serviceId": SERVICE_ID,
                "name": name,
                "value": value,
                "skipDeploys": skip_deploys,
            }
        },
    }
    response = httpx.post(
        RAILWAY_API_URL,
        headers={"Project-Access-Token": token, "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    body = response.json()
    if "errors" in body:
        sys.exit(f"Railway recusou a atualização de {name}: {body['errors']}")
    if not body.get("data", {}).get("variableUpsert"):
        sys.exit(f"Resposta inesperada do Railway ao gravar {name}: {body}")
    print(f"  {name} atualizado (skipDeploys={skip_deploys}).")


def _variable_delete(token: str, name: str) -> None:
    payload = {
        "query": VARIABLE_DELETE_MUTATION,
        "variables": {
            "input": {
                "projectId": PROJECT_ID,
                "environmentId": ENVIRONMENT_ID,
                "serviceId": SERVICE_ID,
                "name": name,
            }
        },
    }
    response = httpx.post(
        RAILWAY_API_URL,
        headers={"Project-Access-Token": token, "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    body = response.json()
    if "errors" in body:
        sys.exit(f"Railway recusou a remoção de {name}: {body['errors']}")
    print(f"  {name} removida.")


def push_to_railway(token: str, master_token: bool, credential_json: str) -> None:
    print("\n== Passo 3/3: enviando pro Railway ==")

    if master_token:
        # NOTEBOOKLM_AUTH_JSON e o modo master-token são MUTUAMENTE EXCLUSIVOS
        # nessa versão da lib (confirmado no código-fonte, _auth/tokens.py):
        # se NOTEBOOKLM_AUTH_JSON estiver setada, a lib sempre usa ela direto
        # e nunca chega a olhar pro master token. Por isso removemos aqui.
        print("  Removendo NOTEBOOKLM_AUTH_JSON (incompatível com o modo master-token)...")
        _variable_delete(token, "NOTEBOOKLM_AUTH_JSON")
        _variable_upsert(
            token, "NOTEBOOKLM_REFRESH_CMD", MASTER_TOKEN_REFRESH_CMD, skip_deploys=True
        )
        _variable_upsert(
            token, "NOTEBOOKLM_MASTER_TOKEN_JSON", credential_json, skip_deploys=False
        )
    else:
        _variable_upsert(token, "NOTEBOOKLM_AUTH_JSON", credential_json, skip_deploys=False)

    print(
        "\nVariável(is) atualizada(s) — redeploy do notebook_dev disparado "
        "automaticamente. Acompanhe em:\n"
        f"https://railway.com/project/{PROJECT_ID}/service/{SERVICE_ID}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--master-token",
        action="store_true",
        help="Usa o modo master-token (auto-renovação) em vez de storage_state.json.",
    )
    parser.add_argument(
        "--account",
        help="E-mail da conta Google dedicada (obrigatório com --master-token).",
    )
    args = parser.parse_args()
    if args.master_token and not args.account:
        parser.error("--master-token exige --account <email>")
    return args


def main() -> None:
    args = parse_args()
    token = load_railway_token()
    run_login(master_token=args.master_token, account=args.account)

    print("\n== Passo 2/3: lendo a credencial gerada ==")
    credential_path = MASTER_TOKEN_PATH if args.master_token else STORAGE_STATE_PATH
    credential_json = read_credential_file(credential_path)

    push_to_railway(token, master_token=args.master_token, credential_json=credential_json)


if __name__ == "__main__":
    main()
