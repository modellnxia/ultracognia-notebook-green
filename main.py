from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="brain_mock")

class ChatRequest(BaseModel):
    message: str

@app.get("/health")
def health():
    return {"status": "ok", "service": "brain_mock"}

@app.post("/chat")
def chat(req: ChatRequest):
    return {"response": f"[MOCK] {req.message}"}
