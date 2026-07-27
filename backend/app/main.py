import logging
import threading
from contextlib import asynccontextmanager
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.api import api_router
from app.core.config import settings
from app.db.base import Base
from app.db.session import engine


logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler: Optional[BackgroundScheduler] = None

    if settings.AUTO_CREATE_TABLES:
        logger.info("Creating any missing database tables")
        Base.metadata.create_all(bind=engine)

    if settings.ENABLE_SOCIAL_HARVESTER or settings.ENABLE_CLUSTER_ANALYSIS:
        scheduler = BackgroundScheduler(timezone="UTC")

        if settings.ENABLE_SOCIAL_HARVESTER:
            # Imported only when enabled so local startup does not require
            # the social harvesting service.
            from scripts.harvest_social import harvest

            scheduler.add_job(
                harvest,
                trigger="interval",
                minutes=settings.SOCIAL_HARVEST_INTERVAL_MINUTES,
                id="social_harvester",
                replace_existing=True,
            )
            logger.info("Social harvester scheduled")

        if settings.ENABLE_CLUSTER_ANALYSIS:
            # Imported only when enabled so AWS/Bedrock is not initialized
            # during a local-ML-only startup.
            from app.services.cluster_analyzer import run_cluster_analysis

            scheduler.add_job(
                run_cluster_analysis,
                trigger="interval",
                minutes=settings.CLUSTER_ANALYSIS_INTERVAL_MINUTES,
                id="cluster_analysis",
                replace_existing=True,
            )
            logger.info("Cluster analysis scheduled")

        scheduler.start()

        if settings.ENABLE_CLUSTER_ANALYSIS:
            threading.Thread(
                target=run_cluster_analysis,
                name="initial-cluster-analysis",
                daemon=True,
            ).start()

    logger.info(
        "Tat-Sahayk backend started with AI provider '%s'",
        settings.AI_PROVIDER,
    )

    yield

    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)

    logger.info("Tat-Sahayk backend stopped")


app = FastAPI(
    title=settings.PROJECT_NAME,
    debug=settings.DEBUG,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(
    api_router,
    prefix=settings.API_V1_PREFIX,
)


@app.get("/", tags=["system"])
def read_root():
    return {
        "service": settings.PROJECT_NAME,
        "status": "running",
        "environment": settings.ENVIRONMENT,
        "ai_provider": settings.AI_PROVIDER,
        "docs": "/docs",
    }


@app.get("/health", tags=["system"])
def health_check():
    return {
        "status": "healthy",
        "service": settings.PROJECT_NAME,
        "ai_provider": settings.AI_PROVIDER,
        "local_ml_enabled": settings.uses_local_ml,
        "bedrock_enabled": settings.uses_bedrock,
    }