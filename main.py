import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from app.routers.report import router as report_router
from app.core.database import create_pool, close_pool
from app.core.settings import settings
from app.scheduler.scheduler import create_scheduler
from fastapi.responses import JSONResponse

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper()),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_pool()
    scheduler = create_scheduler()
    scheduler.start()
    yield
    scheduler.shutdown()
    await close_pool()

app = FastAPI(title="brain_notebooklm", lifespan=lifespan)

@app.middleware("http")
async def validar_acesso(request: Request, call_next):
    path = request.url.path
    if path in ["/", "/docs", "/openapi.json"] or "login" in path:
        return await call_next(request)

    # Preflight de CORS (OPTIONS) não carrega x-api-key — é o próprio
    # navegador perguntando permissão antes da chamada real. Deixa passar
    # direto pro CORSMiddleware tratar (só existe se ENV=local).
    if request.method == "OPTIONS":
        return await call_next(request)

    api_key = request.headers.get("x-api-key");
    API_KEY = os.getenv("API_KEY", "")

    if api_key != API_KEY:
        return JSONResponse(status_code=403, content={"detail": "Não autorizado"})

    return await call_next(request)

if settings.ENV == "local":
    # CORS permissivo só em ambiente local — permite que a interface de teste
    # (arquivo HTML aberto direto no navegador) chame a API. A autenticação
    # real continua sendo o x-api-key; CORS só libera o navegador a ler a
    # resposta. Nunca habilitado por padrão (settings.ENV = "production").
    # Registrado DEPOIS do validar_acesso de propósito: no Starlette, o
    # middleware adicionado por último fica mais "externo" e roda primeiro —
    # precisa vir depois pra interceptar o preflight OPTIONS antes da auth.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(report_router)

@app.get("/health")
def health():
    return {"status": "ok", "service": "brain_notebooklm"}
