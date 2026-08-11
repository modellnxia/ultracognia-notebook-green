"""
Automatiza a renovação da sessão do NotebookLM usada em produção (Railway).

O que faz, em ordem:
    1. Roda `notebooklm login` — abre o navegador, você loga manualmente com a
       conta Google de produção (esse passo não dá pra automatizar, é a própria
       Google que exige interação humana aqui).
    2. Lê o storage_state.json gerado pela lib em ~/.notebooklm/.
    3. Atualiza a variável NOTEBOOKLM_AUTH_JSON do serviço `notebook_dev` no
       Railway via API (isso já dispara redeploy automático do serviço — não
       precisa de nenhum passo extra de restart).

Uso (de dentro do venv do projeto, pra achar o `notebooklm` no PATH):
    .venv\\Scripts\\python.exe scripts\\refresh_notebooklm_auth.py

Pré-requisito: scripts/.railway_token contendo o Project Token do Railway
(uma linha só, sem aspas, sem quebra de linha extra). Esse arquivo é
gitignored — nunca sobe pro repositório.
"""

import json
import subprocess
import sys
from pathlib import Path

import httpx

RAILWAY_API_URL = "https://backboard.railway.com/graphql/v2"

# IDs do projeto "MODELLNX-DEV" / serviço "notebook_dev" no Railway.
# Descobertos via API em 2026-08-11 — só mudam se o serviço for recriado.
PROJECT_ID = "0cd78ec6-01bb-4c7d-94ca-327cae729bef"
ENVIRONMENT_ID = "5c113100-8914-474e-b8a5-287c1249bcdf"
SERVICE_ID = "d391d420-7488-4ce7-841c-643e17d3f75b"  # notebook_dev

TOKEN_FILE = Path(__file__).parent / ".railway_token"
STORAGE_STATE_PATH = (
    Path.home() / ".notebooklm" / "profiles" / "default" / "storage_state.json"
)

VARIABLE_UPSERT_MUTATION = """
mutation($input: VariableUpsertInput!) {
  variableUpsert(input: $input)
}
"""


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


def run_notebooklm_login() -> None:
    print(
        "\n== Passo 1/3: login no NotebookLM ==\n"
        "Vai abrir o navegador. Loga com a conta Google de produção, espera a "
        "home do NotebookLM carregar, volta aqui e confirma no terminal.\n"
    )
    result = subprocess.run([notebooklm_executable(), "login"])
    if result.returncode != 0:
        sys.exit(f"`notebooklm login` terminou com código {result.returncode}. Abortando.")


def read_storage_state() -> str:
    if not STORAGE_STATE_PATH.exists():
        sys.exit(f"Não encontrei {STORAGE_STATE_PATH} depois do login. Algo deu errado.")
    content = STORAGE_STATE_PATH.read_text(encoding="utf-8")
    json.loads(content)  # valida que é JSON de verdade antes de mandar pro Railway
    return content


def push_to_railway(token: str, auth_json: str) -> None:
    print("\n== Passo 3/3: enviando pro Railway ==")
    payload = {
        "query": VARIABLE_UPSERT_MUTATION,
        "variables": {
            "input": {
                "projectId": PROJECT_ID,
                "environmentId": ENVIRONMENT_ID,
                "serviceId": SERVICE_ID,
                "name": "NOTEBOOKLM_AUTH_JSON",
                "value": auth_json,
                # skipDeploys NÃO setado de propósito: queremos o redeploy
                # automático do notebook_dev com a sessão nova.
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
        sys.exit(f"Railway recusou a atualização: {body['errors']}")
    if not body.get("data", {}).get("variableUpsert"):
        sys.exit(f"Resposta inesperada do Railway: {body}")
    print(
        "NOTEBOOKLM_AUTH_JSON atualizado — redeploy do notebook_dev disparado "
        "automaticamente. Acompanhe em:\n"
        f"https://railway.com/project/{PROJECT_ID}/service/{SERVICE_ID}"
    )


def main() -> None:
    token = load_railway_token()
    run_notebooklm_login()
    print("\n== Passo 2/3: lendo a sessão gerada ==")
    auth_json = read_storage_state()
    push_to_railway(token, auth_json)


if __name__ == "__main__":
    main()
