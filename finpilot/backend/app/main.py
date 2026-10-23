from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.integrations.fineract.client import FineractClient
from app.mcp import server as mcp_module
from app.api.routes import router as api_router

configure_logging(get_settings().log_level)


@asynccontextmanager
async def lifespan(
    app: FastAPI,
    settings: Settings,
    session_manager: StreamableHTTPSessionManager | None = None,
) -> AsyncIterator[None]:
    """Comparte el lifespan entre FastAPI y el servidor MCP.

    Cuando MCP está habilitado: crea el ``FineractClient`` compartido, lo
    registra como proveedor de las herramientas y arranca el gestor de
    sesiones del transporte *streamable HTTP*. Al salir, limpia el proveedor
    y cierra el cliente.

    Se arranca el gestor asociado a esta app (``session_manager``) y no el
    global de ``mcp_server``: cada ``build_mcp_app`` crea uno nuevo, de modo
    que usar el global rompería apps construidas antes.
    """
    if not settings.mcp_enabled:
        yield
        return
    fineract = FineractClient(settings)
    mcp_module.set_client_provider(lambda: fineract)
    try:
        if session_manager is None:
            yield
        else:
            async with session_manager.run():
                yield
    finally:
        mcp_module.set_client_provider(None)
        await fineract.aclose()


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    mcp_session_manager: StreamableHTTPSessionManager | None = None

    if resolved.mcp_enabled:
        mcp_app, mcp_session_manager = mcp_module.build_mcp_app(
            dev_token=resolved.mcp_dev_token
        )

    @asynccontextmanager
    async def app_lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with lifespan(app, resolved, mcp_session_manager):
            yield

    docs_enabled = resolved.api_docs_enabled
    application = FastAPI(
        title="FinPilot API",
        description="Agente de operaciones financieras en un entorno simulado",
        version="0.1.0",
        lifespan=app_lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    application.include_router(api_router)

    @application.get("/health")
    async def health_check():
        return {"status": "ok"}

    if resolved.mcp_enabled:
        application.mount("/mcp", mcp_app)
        application.add_middleware(mcp_module.MCPPathNormalizer, path="/mcp")

    return application


app = create_app()


if __name__ == "__main__":  # pragma: no cover - punto de entrada de desarrollo
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
