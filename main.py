import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.routers.report import router as report_router
from app.core.database import create_pool, close_pool
from app.scheduler.scheduler import create_scheduler

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

app.include_router(report_router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "brain_notebooklm"}
