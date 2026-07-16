from datetime import date, datetime
import asyncpg
import sys
from pathlib import Path
import json
from collections import defaultdict

from app.core.settings import settings
from app.repositories.users import UserRepository
from app.repositories.conversations import ConversationMessageRepository
from app.services.report_service import _join_messages, _build_notebook_title

async def gerar_conversas():
    today = date.today()
    print(f"Iniciando geração das conversas até {today.strftime("%d/%m/%Y")}")

    conn = await asyncpg.connect(dsn=settings.DATABASE_URL)
    try:
        user_ids = await UserRepository(conn).fetch_users_with_messages()

        created = 0
        skipped = 0

        for user_id in user_ids:
            try:
                
                # 2. Busca nome do usuário
                user_row = await conn.fetchrow("SELECT name FROM users WHERE id = $1", user_id)
                if not user_row:
                    raise ValueError(f"Usuário não encontrado: {user_id}")
                user_name = user_row["name"]

                # 3. Busca mensagens do dia ou range
                conv_repo = ConversationMessageRepository(conn)
                rows = await conv_repo.fetch_messages_by_user_and_date_range(
                    user_id, today
                )

                messages = [f"[{row['role'].upper()}] {row['content']}" for row in rows]
                unified_text = _join_messages(messages)
                
                titled = _build_notebook_title(user_name, today)
                
                nome_do_arquivo = f"{titled}.txt"

                # Verifica se o script está rodando como um executável do PyInstaller
                if getattr(sys, "frozen", False):
                    # Caminho da pasta onde o arquivo .exe está executando
                    diretorio_atual = Path(sys.executable).parent
                else:
                    # Caminho normal se for executado direto pelo código fonte (.py)
                    diretorio_atual = Path(__file__).resolve().parent

                # Define o caminho completo do novo arquivo txt
                caminho_arquivo = diretorio_atual / "arquivos" 

                caminho_arquivo.parent.mkdir(parents=True, exist_ok=True)

                caminho_arquivo = caminho_arquivo / nome_do_arquivo.replace("/", "-")

                # Cria e escreve no arquivo texto
                with open(caminho_arquivo, "w", encoding="utf-8") as arquivo:
                    arquivo.write(unified_text)

                print(f"Arquivo '{nome_do_arquivo}' foi criado ou substituído.")

            except Exception as e:
                causa_raiz = e.__cause__ or e
                print(f"Erro ao criar conversas para user_id={user_id} {user_name} - {causa_raiz}")

        print(f"Finalizado - criados: {created}")

    finally:
        await conn.close()


async def gerar_conversas_json():
    today = date.today()
    print(f"Iniciando geração das conversas json até {today.strftime("%d/%m/%Y")}")

    conn = await asyncpg.connect(dsn=settings.DATABASE_URL)
    try:
        user_ids = await UserRepository(conn).fetch_users_with_messages()

        created = 0
        skipped = 0
                        
        nome_do_arquivo = f"conversas.json"

        # Verifica se o script está rodando como um executável do PyInstaller
        if getattr(sys, "frozen", False):
            # Caminho da pasta onde o arquivo .exe está executando
            diretorio_atual = Path(sys.executable).parent
        else:
            # Caminho normal se for executado direto pelo código fonte (.py)
            diretorio_atual = Path(__file__).resolve().parent
        caminho_arquivo = diretorio_atual / "arquivos" 

        caminho_arquivo.parent.mkdir(parents=True, exist_ok=True)

        for user_id in user_ids:
            try:                    
                # 2. Busca nome do usuário
                user_row = await conn.fetchrow("SELECT name FROM users WHERE id = $1", user_id)
                if not user_row:
                    raise ValueError(f"Usuário não encontrado: {user_id}")
                user_name = user_row["name"]

                # 3. Busca mensagens do dia ou range
                conv_repo = ConversationMessageRepository(conn)
                rows = await conv_repo.fetch_messages_by_user_and_date_range_json(
                    user_id, today
                )

                caminho_arquivo_salvar = caminho_arquivo / f"{user_name.replace('/', '-')}.json"
                with open(caminho_arquivo_salvar, "w", encoding="utf-8") as arquivo:
                    dados_processados = transformar_para_json(rows)
                    json_final = json.dumps(dados_processados, indent=2, ensure_ascii=False)
                    #print(json_final)
                
                    arquivo.write(json_final)
            
            except Exception as e:
                causa_raiz = e.__cause__ or e
                print(f"Erro ao criar conversas para user_id={user_id} {user_name} - {causa_raiz}")

            print(f"Finalizado - criados: {created}")

    finally:
        await conn.close()        

def transformar_para_json(dados):
    conversas_map = {}

    for row in dados:
        # Extração dos campos (ajuste os índices conforme a sua tupla/objeto de retorno)
        role, content, created_at, title = row
        
        # Converter created_at para timestamp (segundos) se já não for
        # Se for um datetime do python, use .timestamp()
        if isinstance(created_at, datetime):
            ts = int(created_at.timestamp())
        else:
            ts = int(created_at) # Ajuste se vier de outra forma

        # Se a conversa ainda não está no mapa, cria a estrutura inicial
        if title not in conversas_map:
            conversas_map[title] = {
                "title": title,
                "models": ["ID-DO-AGENTE-AQUI"],
                "created_at": ts, # O created_at da conversa é o da primeira mensagem
                "updated_at": ts, # Inicializa igual, será atualizado depois
                "messages": []
            }
        
        # Monta a estrutura da mensagem
        msg = {
            "role": role,
            "content": content,
            "timestamp": ts
        }
        
        if role == "assistant":
            msg["model"] = "ID-DO-AGENTE-AQUI"
            
        conversas_map[title]["messages"].append(msg)
        
        # LÓGICA DO UPDATED_AT:
        # Comparamos o timestamp da mensagem atual com o updated_at salvo.
        # Se o da mensagem for maior, ele é o mais recente.
        if ts > conversas_map[title]["updated_at"]:
            conversas_map[title]["updated_at"] = ts

    return list(conversas_map.values())    