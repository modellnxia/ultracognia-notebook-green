import logging
import os
from fastapi import FastAPI
from app.routers.report import router as report_router

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper()),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(title="brain_notebooklm")

app.include_router(report_router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "brain_notebooklm"}
