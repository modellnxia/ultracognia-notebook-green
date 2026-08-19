"""
Camada de LLM — ninguém fora deste pacote deve importar SDK/cliente de
provedor de IA diretamente. Todo código de negócio chama só
`app.decks.llm.client.generate_text()`.
"""
