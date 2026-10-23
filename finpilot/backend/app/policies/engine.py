from typing import Any
from pydantic import BaseModel, ConfigDict, ValidationError
from app.tools.registry import READ_TOOLS
from app.core.config import get_settings

class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

def validate_tool_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if {"approvalId", "payloadHash"} & arguments.keys():
        raise ValueError("Los metadatos de aprobación no forman parte de los argumentos de operación")
    if name in READ_TOOLS:
        schema = READ_TOOLS[name][0]
    elif name == "transfer_funds":
        from app.tools.schemas import TransferFundsArgs
        schema = TransferFundsArgs
    elif name == "update_fraud_status":
        from app.tools.schemas import UpdateFraudStatusArgs
        schema = UpdateFraudStatusArgs
    else:
        raise ValueError(f"Herramienta desconocida: {name}")
    result = schema.model_validate(arguments).model_dump(by_alias=True, exclude_none=True)
    if name == "transfer_funds":
        if result["fromAccountId"] == result["toAccountId"]:
            raise ValueError("Las cuentas de origen y destino deben ser distintas")
        amount = result["amount"]
        if amount > float(get_settings().policy_max_transaction_amount):
            raise ValueError("El monto supera POLICY_MAX_TRANSACTION_AMOUNT")
    return result

def classify_risk(name: str) -> tuple[str, str]:
    return ("LOW", "READ_ALLOWLIST") if name in READ_TOOLS else ("HIGH_SENSITIVE", "NOT_IN_READ_ALLOWLIST")
