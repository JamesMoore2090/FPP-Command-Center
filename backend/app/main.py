import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import __version__
from .api import controllers, hosts, ws
from .collector import Collector
from .config import get_settings
from .db import Base, SessionLocal, engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.database_url.startswith("sqlite"):   # dev convenience; production uses Alembic
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    collector = Collector(settings, SessionLocal)
    app.state.collector = collector
    if settings.run_collector:
        await collector.start()
    else:
        await collector.reload_hosts()
    yield
    await collector.stop()


app = FastAPI(title="FPP Command Center", version=__version__, lifespan=lifespan,
              docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
app.include_router(hosts.router)
app.include_router(controllers.router)
app.include_router(ws.router)


@app.get("/api/health")
async def health():
    return {"ok": True, "version": __version__}
