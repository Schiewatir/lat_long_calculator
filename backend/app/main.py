import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings


def create_app() -> FastAPI:
    from app.api import admin, distance, events, export, jobs, rows, system
    from app.auth import routes as auth_routes

    settings = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx logs full request URLs at INFO, which would include provider API keys (`key=` query params).
    logging.getLogger("httpx").setLevel(logging.WARNING)
    app = FastAPI(title="Geocoding Workbench", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for module in (system, auth_routes, admin, jobs, rows, events, export, distance):
        app.include_router(module.router, prefix="/api")
    app.include_router(admin.config_router, prefix="/api")
    return app


app = create_app()
