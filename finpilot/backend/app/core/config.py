from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Any

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración de FinPilot cargada desde variables de entorno y/o `.env`.

    Los secretos se modelan con ``SecretStr`` para que nunca aparezcan en
    representaciones, logs o volcados accidentales. La validación TLS permanece
    activa por defecto: desactivarla debe ser una decisión explícita.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Conexión con Apache Fineract ---
    fineract_base_url: str = Field(
        default="https://localhost:8443/fineract-provider",
        description="URL base del contexto de Fineract (sin el prefijo /api/v1).",
    )
    fineract_tenant_id: str = Field(default="default")
    fineract_username: str = Field(default="")
    fineract_password: SecretStr = Field(default=SecretStr(""))
    fineract_timeout_seconds: float = Field(default=10.0, gt=0)
    fineract_verify_tls: bool = Field(
        default=True,
        description="Validar el certificado TLS. Poner en false solo para el entorno local de desarrollo.",
    )

    # --- Autorización temporal de aprobaciones ---
    approval_api_token: SecretStr = Field(default=SecretStr(""))
    approval_ttl_seconds: int = Field(default=900, gt=0)

    # --- Límites de operaciones ---
    policy_max_transaction_amount: Decimal = Field(default=Decimal("1000.00"), ge=0)
    policy_approval_threshold_amount: Decimal = Field(default=Decimal("100.00"), ge=0)

    # --- Logging ---
    log_level: str = Field(default="INFO")

    # --- Documentación de la API (deshabilitada por defecto) ---
    api_docs_enabled: bool = Field(
        default=False,
        description=(
            "Expone /docs, /redoc y /openapi.json. Deshabilitado por defecto para "
            "minimizar la superficie expuesta; habilitar solo en desarrollo local."
        ),
    )

    # --- Servidor MCP (deshabilitado por defecto) ---
    mcp_enabled: bool = Field(
        default=False,
        description=(
            "Monta el endpoint /mcp (streamable HTTP). En desarrollo debe "
            "accederse solo desde localhost; no exponer en una red accesible."
        ),
    )
    mcp_dev_token: SecretStr = Field(
        default=SecretStr(""),
        description=(
            "Token opcional exigido en la cabecera X-MCP-Token al montar /mcp. "
            "Es una guarda de desarrollo, no un mecanismo de autorización."
        ),
    )

    @field_validator("fineract_base_url")
    @classmethod
    def _normalize_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized:
            raise ValueError("FINERACT_BASE_URL no puede estar vacío")
        return normalized

    @field_validator("log_level")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        return value.strip().upper()

    def safe_summary(self) -> dict[str, Any]:
        """Resumen sin secretos apto para logs de arranque."""
        return {
            "fineract_base_url": self.fineract_base_url,
            "fineract_tenant_id": self.fineract_tenant_id,
            "fineract_username": self.fineract_username,
            "fineract_password_set": bool(self.fineract_password.get_secret_value()),
            "fineract_timeout_seconds": self.fineract_timeout_seconds,
            "fineract_verify_tls": self.fineract_verify_tls,
            "approval_api_token_set": bool(self.approval_api_token.get_secret_value()),
            "approval_ttl_seconds": self.approval_ttl_seconds,
            "policy_max_transaction_amount": str(self.policy_max_transaction_amount),
            "policy_approval_threshold_amount": str(self.policy_approval_threshold_amount),
            "mcp_enabled": self.mcp_enabled,
            "mcp_dev_token_set": bool(self.mcp_dev_token.get_secret_value()),
            "api_docs_enabled": self.api_docs_enabled,
            "log_level": self.log_level,
        }


@lru_cache
def get_settings() -> Settings:
    """Devuelve la configuración (cacheada) de la aplicación."""
    return Settings()
