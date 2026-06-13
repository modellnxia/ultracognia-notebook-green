import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from app.routers.report import router as report_router
from app.core.database import create_pool, close_pool
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
        
    api_key = request.headers.get("x-api-key");
    API_KEY = os.getenv("API_KEY", "")

    if api_key != API_KEY:
        return JSONResponse(status_code=403, content={"detail": "Não autorizado"})

    return await call_next(request)

app.include_router(report_router)

@app.get("/health")
def health():
    return {"status": "ok", "service": "brain_notebooklm"}
