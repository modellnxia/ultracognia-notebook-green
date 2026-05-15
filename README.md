Teste
curl -s -X POST http://localhost:8050/report/generate -H "Content-Type: application/json" -d '{
     "notebook_title": "Teste de Relatório Textual",
     "messages": [
       "Usuário: Qual é a capital do Brasil?",
       "Assistente: A capital do Brasil é Brasília.",
       "Usuário: E qual é a maior cidade?",
       "Assistente: A maior cidade do Brasil é São Paulo."
]
}' | jq
