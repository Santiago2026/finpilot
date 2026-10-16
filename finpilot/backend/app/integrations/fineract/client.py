from __future__ import annotations

import logging
from types import TracebackType
from typing import Any, Self

import httpx

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Raíz de la API REST de Fineract, relativa a FINERACT_BASE_URL.
DEFAULT_API_PREFIX = "api/v1"


class FineractError(Exception):
    """Error base de la integración con Fineract."""


class FineractConfigurationError(FineractError):
    """Falta o es inválida una configuración obligatoria para comunicarse con Fineract."""


class FineractConnectionError(FineractError):
    """No fue posible establecer conexión con Fineract."""


class FineractTimeoutError(FineractError):
    """La petición superó el tiempo máximo configurado."""


class FineractHTTPError(FineractError):
    """Fineract respondió con un estado HTTP no exitoso.

    No incorpora el cuerpo de la respuesta: puede contener datos personales o
    financieros que el filtro de logs no detecte. Solo conserva el código HTTP.
    """

    def __init__(self, status_code: int, message: str | None = None) -> None:
        super().__init__(message or f"Fineract respondió con HTTP {status_code}")
        self.status_code = status_code


class FineractResponseError(FineractError):
    """La respuesta de Fineract no pudo interpretarse como se esperaba."""


class FineractClient:
    """Cliente HTTP asíncrono y de solo lectura para la API REST de Fineract.

    Centraliza la URL base, la autenticación Basic, la cabecera de tenant y el
    manejo de errores. En esta etapa solo expone peticiones GET.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._validate_settings(self._settings)
        self._client = httpx.AsyncClient(
            base_url=self._settings.fineract_base_url,
            auth=(
                self._settings.fineract_username,
                self._settings.fineract_password.get_secret_value(),
            ),
            headers={
                "Fineract-Platform-TenantId": self._settings.fineract_tenant_id,
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(self._settings.fineract_timeout_seconds),
            verify=self._settings.fineract_verify_tls,
            transport=transport,
        )

    @staticmethod
    def _validate_settings(settings: Settings) -> None:
        missing: list[str] = []
        if not settings.fineract_username:
            missing.append("FINERACT_USERNAME")
        if not settings.fineract_password.get_secret_value():
            missing.append("FINERACT_PASSWORD")
        if not settings.fineract_tenant_id:
            missing.append("FINERACT_TENANT_ID")
        if missing:
            raise FineractConfigurationError(
                "Configuración de Fineract incompleta: falta " + ", ".join(missing)
            )

    def _build_url(self, path: str) -> str:
        return f"{self._settings.fineract_base_url}/{path.lstrip('/')}"

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        """Realiza un GET y devuelve el JSON de la respuesta.

        ``path`` es relativo a ``FINERACT_BASE_URL`` (por ejemplo ``api/v1/clients``).
        """
        url = self._build_url(path)
        logger.debug("GET %s", url)
        try:
            response = await self._client.get(url, params=params)
        except httpx.TimeoutException as exc:
            raise FineractTimeoutError(
                "Se agotó el tiempo de espera "
                f"({self._settings.fineract_timeout_seconds}s) al consultar Fineract"
            ) from exc
        except httpx.TransportError as exc:
            raise FineractConnectionError(
                f"No se pudo conectar con Fineract ({type(exc).__name__})"
            ) from exc
        return self._handle_response(response)

    def _handle_response(self, response: httpx.Response) -> Any:
        if response.status_code >= 400:
            raise FineractHTTPError(response.status_code)
        try:
            return response.json()
        except ValueError as exc:
            raise FineractResponseError(
                "La respuesta de Fineract no es JSON válido"
            ) from exc

    @property
    def is_closed(self) -> bool:
        return self._client.is_closed

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()
