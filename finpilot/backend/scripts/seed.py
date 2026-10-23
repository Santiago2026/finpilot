"""Seed idempotente de usuarios FinPilot y sandbox sintético Apache Fineract."""
from __future__ import annotations

import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings, get_settings
from app.db.models import User


SEED_DATE = "01 January 2024"
DATE_FORMAT = "dd MMMM yyyy"
LOCALE = "en"
TARGET_BALANCE = Decimal("1000")
PRODUCT_SHORT_NAME = "FPS1"
LEGAL_FORM_PERSON_ID = 1
IDS_DOCUMENT = Path(__file__).resolve().parents[1] / "docs" / "sandbox-ids.md"


class FineractSeedError(RuntimeError):
    """Error de comunicación o de datos durante el seed de Fineract."""


class FineractSeedClient:
    """Cliente pequeño de seed que conserva el cuerpo de errores de Fineract."""

    def __init__(self, settings: Settings) -> None:
        self.base_url = settings.fineract_base_url.rstrip("/")
        self.client = httpx.AsyncClient(
            base_url=self.base_url + "/",
            auth=(settings.fineract_username, settings.fineract_password.get_secret_value()),
            headers={
                "Fineract-Platform-TenantId": settings.fineract_tenant_id,
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(settings.fineract_timeout_seconds),
            verify=settings.fineract_verify_tls,
        )

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def post(
        self,
        path: str,
        *,
        json_body: dict[str, Any],
        params: dict[str, Any] | None = None,
    ) -> Any:
        return await self._request("POST", path, json_body=json_body, params=params)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        try:
            response = await self.client.request(
                method,
                urljoin("api/v1/", path.lstrip("/")),
                json=json_body,
                params=params,
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            raise FineractSeedError(
                f"Fineract no disponible en {self.base_url}. Verifica que el contenedor esté corriendo."
            ) from exc
        except httpx.HTTPError as exc:
            raise FineractSeedError(
                f"Error HTTP al conectar con Fineract en {self.base_url}: {type(exc).__name__}: {exc}"
            ) from exc

        if response.status_code >= 400:
            detail = response.text.strip() or response.reason_phrase
            try:
                error = response.json()
                messages = [
                    item.get("defaultUserMessage") or item.get("developerMessage")
                    for item in error.get("errors", [])
                    if isinstance(item, dict)
                ]
                detail = "; ".join(message for message in messages if message) or (
                    error.get("defaultUserMessage") or error.get("developerMessage") or detail
                )
            except (ValueError, AttributeError):
                pass
            raise FineractSeedError(f"Fineract respondió HTTP {response.status_code}: {detail}")
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise FineractSeedError(
                f"Fineract devolvió HTTP {response.status_code} con una respuesta JSON inválida: {response.text[:500]}"
            ) from exc

    async def aclose(self) -> None:
        await self.client.aclose()


def _items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("pageItems", "data", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _resource_id(response: Any, resource: str) -> int:
    if not isinstance(response, dict):
        raise FineractSeedError(f"Respuesta de creación inválida para {resource}: {response!r}")
    ident = response.get("resourceId") or response.get("clientId") or response.get("savingsId")
    if ident is None:
        raise FineractSeedError(f"Fineract no devolvió resourceId al crear {resource}: {response!r}")
    return int(ident)


def _choice_id(options: Any, *, labels: tuple[str, ...], description: str) -> int:
    if not isinstance(options, list):
        raise FineractSeedError(f"La plantilla Fineract no incluye opciones de {description}")
    normalized_labels = {label.casefold() for label in labels}
    for option in options:
        if not isinstance(option, dict):
            continue
        label = str(option.get("label") or option.get("name") or option.get("value") or "").casefold()
        if label in normalized_labels and option.get("id") is not None:
            return int(option["id"])
    raise FineractSeedError(
        f"La plantilla Fineract no ofrece una opción válida de {description}: {options!r}"
    )


def _default_office_id(template: dict[str, Any]) -> int:
    if template.get("officeId") is not None:
        return int(template["officeId"])
    options = template.get("officeOptions")
    if isinstance(options, list) and options:
        selected = next((item for item in options if item.get("selected")), options[0])
        if selected.get("id") is not None:
            return int(selected["id"])
    raise FineractSeedError("La plantilla de clientes no incluye una oficina por defecto")


async def _ensure_product(api: FineractSeedClient) -> int:
    existing = next(
        (item for item in _items(await api.get("savingsproducts")) if item.get("shortName") == PRODUCT_SHORT_NAME),
        None,
    )
    if existing:
        print(f"Savings product existente: shortName={PRODUCT_SHORT_NAME}, id={existing['id']}")
        return int(existing["id"])

    await api.get("savingsproducts/template")
    body = {
        "name": "FinPilot Synthetic Savings",
        "shortName": PRODUCT_SHORT_NAME,
        "description": "Synthetic sandbox savings product for FinPilot HITL tests",
        "currencyCode": "USD",
        "digitsAfterDecimal": 2,
        "inMultiplesOf": 1,
        "nominalAnnualInterestRate": 0,
        "interestCompoundingPeriodType": 1,
        "interestPostingPeriodType": 4,
        "interestCalculationType": 1,
        "interestCalculationDaysInYearType": 365,
        "accountingRule": 1,
        "locale": LOCALE,
    }
    ident = _resource_id(await api.post("savingsproducts", json_body=body), "savings product")
    print(f"Savings product creado: shortName={PRODUCT_SHORT_NAME}, id={ident}")
    return ident


async def _find_client(api: FineractSeedClient, display_name: str) -> dict[str, Any] | None:
    result = await api.get("clients", params={"displayName": display_name, "limit": 100, "offset": 0})
    return next((item for item in _items(result) if item.get("displayName") == display_name), None)


async def _ensure_client(api: FineractSeedClient, number: int) -> int:
    display_name = f"FinPilot Client {number}"
    existing = await _find_client(api, display_name)
    if existing:
        if not existing.get("active", False):
            raise FineractSeedError(
                f"El cliente existente {display_name!r} (id={existing.get('id')}) no está activo; no se duplicará."
            )
        print(f"Cliente existente: {display_name}, id={existing['id']}")
        return int(existing["id"])

    template = await api.get("clients/template")
    legal_options = template.get("legalFormOptions")
    if legal_options:
        legal_form_id = _choice_id(
            legal_options,
            labels=("person", "individual", "natural person"),
            description="forma legal Persona",
        )
    else:
        # Algunas versiones de Fineract devuelven legalFormOptions=null aunque
        # legalFormId sea obligatorio. En ese contrato, id 1 es el enum Persona.
        legal_form_id = LEGAL_FORM_PERSON_ID
    body = {
        "officeId": _default_office_id(template),
        "legalFormId": legal_form_id,
        "firstname": "FinPilot",
        "lastname": f"Client {number}",
        "externalId": f"FINPILOT-SYNTHETIC-CLIENT-{number}",
        "active": True,
        "activationDate": SEED_DATE,
        "dateFormat": DATE_FORMAT,
        "locale": LOCALE,
    }
    ident = _resource_id(await api.post("clients", json_body=body), display_name)
    print(f"Cliente creado: {display_name}, id={ident}")
    return ident


async def _find_savings_account(
    api: FineractSeedClient, client_id: int, product_id: int
) -> dict[str, Any] | None:
    results = await api.get("savingsaccounts", params={"clientId": client_id, "limit": 100, "offset": 0})
    return next(
        (
            item for item in _items(results)
            if int(item.get("clientId", -1)) == client_id
            and int(item.get("productId", item.get("savingsProductId", -1))) == product_id
        ),
        None,
    )


async def _create_savings_account(
    api: FineractSeedClient, client_id: int, product_id: int
) -> int:
    template = await api.get("savingsaccounts/template", params={"clientId": client_id})
    body = {
        "clientId": client_id,
        "productId": product_id,
        "submittedOnDate": SEED_DATE,
        "dateFormat": DATE_FORMAT,
        "locale": LOCALE,
    }
    return _resource_id(await api.post("savingsaccounts", json_body=body), "savings account")


async def _account_detail(api: FineractSeedClient, savings_id: int) -> dict[str, Any]:
    result = await api.get(f"savingsaccounts/{savings_id}")
    if not isinstance(result, dict):
        raise FineractSeedError(f"Fineract devolvió una cuenta inválida para id={savings_id}")
    return result


def _status(account: dict[str, Any]) -> str:
    status = account.get("status", "")
    return str(status.get("value", "") if isinstance(status, dict) else status).casefold()


async def _ensure_active(api: FineractSeedClient, savings_id: int) -> None:
    account = await _account_detail(api, savings_id)
    status = _status(account)
    if status == "active":
        return
    if status in {"submitted and pending approval", "submitted and awaiting approval", "submitted"}:
        await api.post(
            f"savingsaccounts/{savings_id}?command=approve",
            json_body={"approvedOnDate": SEED_DATE, "dateFormat": DATE_FORMAT, "locale": LOCALE},
        )
        account = await _account_detail(api, savings_id)
        status = _status(account)
    if status == "approved":
        await api.post(
            f"savingsaccounts/{savings_id}?command=activate",
            json_body={"activatedOnDate": SEED_DATE, "dateFormat": DATE_FORMAT, "locale": LOCALE},
        )
        return
    if status != "active":
        raise FineractSeedError(
            f"No se puede activar savings account id={savings_id}: estado actual {status or 'desconocido'!r}"
        )


async def _ensure_minimum_balance(api: FineractSeedClient, savings_id: int) -> Decimal:
    account = await _account_detail(api, savings_id)
    balance = Decimal(str(account.get("summary", {}).get("accountBalance", 0) or 0))
    difference = TARGET_BALANCE - balance
    if difference > 0:
        template = await api.get(
            f"savingsaccounts/{savings_id}/transactions/template",
            params={"command": "deposit"},
        )
        payment_type_id = _choice_id(
            template.get("paymentTypeOptions"),
            labels=("money transfer",),
            description="método de pago Money Transfer",
        )
        await api.post(
            f"savingsaccounts/{savings_id}/transactions",
            params={"command": "deposit"},
            json_body={
                "transactionDate": SEED_DATE,
                "transactionAmount": float(difference),
                "paymentTypeId": payment_type_id,
                "dateFormat": DATE_FORMAT,
                "locale": LOCALE,
            },
        )
    refreshed = await _account_detail(api, savings_id)
    return Decimal(str(refreshed.get("summary", {}).get("accountBalance", 0) or 0))


async def seed_fineract(settings: Settings) -> dict[str, Any]:
    api = FineractSeedClient(settings)
    try:
        product_id = await _ensure_product(api)
        client_ids = [await _ensure_client(api, number) for number in (1, 2)]
        account_ids: list[int] = []
        balances: list[Decimal] = []
        for client_id in client_ids:
            account = await _find_savings_account(api, client_id, product_id)
            savings_id = int(account["id"]) if account else await _create_savings_account(api, client_id, product_id)
            if account:
                print(f"Savings account existente: clientId={client_id}, productId={product_id}, id={savings_id}")
            await _ensure_active(api, savings_id)
            balance = await _ensure_minimum_balance(api, savings_id)
            account_ids.append(savings_id)
            balances.append(balance)
        return {
            "productId": product_id,
            "clientId1": client_ids[0],
            "clientId2": client_ids[1],
            "savingsId1": account_ids[0],
            "savingsId2": account_ids[1],
            "balances": [str(balance) for balance in balances],
        }
    finally:
        await api.aclose()


def seed_users(settings: Settings) -> None:
    url = settings.database_url.replace("sqlite+aiosqlite://", "sqlite://", 1)
    engine = create_engine(url)
    try:
        with Session(engine) as session:
            for username, role in (("admin", "ADMIN"), ("approver", "APPROVER"), ("operator", "OPERATOR")):
                if session.scalar(select(User).where(User.username == username)) is None:
                    session.add(User(username=username, role=role))
            session.commit()
    finally:
        engine.dispose()


def _write_ids(result: dict[str, Any]) -> None:
    IDS_DOCUMENT.parent.mkdir(parents=True, exist_ok=True)
    IDS_DOCUMENT.write_text(
        "# IDs de datos sintéticos de Fineract\n\n"
        "Actualizados por `python scripts/seed.py`. El proceso es idempotente; "
        "las fechas de creación, aprobación, activación y depósito usan 2024-01-01.\n\n"
        "| Recurso | ID | Estado / saldo actual |\n|---|---:|---|\n"
        f"| Savings product `{PRODUCT_SHORT_NAME}` | `{result['productId']}` | Disponible |\n"
        f"| FinPilot Client 1 | `{result['clientId1']}` | Activo |\n"
        f"| FinPilot Client 2 | `{result['clientId2']}` | Activo |\n"
        f"| Savings account 1 | `{result['savingsId1']}` | Activa; saldo `{result['balances'][0]}` USD |\n"
        f"| Savings account 2 | `{result['savingsId2']}` | Activa; saldo `{result['balances'][1]}` USD |\n\n"
        "Las fechas se envían como `01 January 2024` junto con `dateFormat="
        f"`{DATE_FORMAT}` y `locale=`{LOCALE}` para evitar diferencias de zona horaria.\n"
    )


def main() -> None:
    settings = get_settings()
    seed_users(settings)
    try:
        result = asyncio.run(seed_fineract(settings))
    except FineractSeedError as exc:
        raise SystemExit(str(exc)) from exc
    _write_ids(result)
    print("\nSeed Fineract completado; entorno listo para HITL:")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"IDs documentados en: {IDS_DOCUMENT}")


if __name__ == "__main__":
    main()
