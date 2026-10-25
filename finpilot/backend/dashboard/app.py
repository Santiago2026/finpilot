"""Consola Streamlit de FinPilot.

Objetivo de la interfaz:
- Enviar operaciones en lenguaje natural o con un plan estructurado.
- Mostrar el resultado en lenguaje claro (por ejemplo, el saldo de una cuenta)
  y dejar el JSON del plan y de la respuesta disponible bajo demanda.
- Aprobar o rechazar operaciones sensibles (flujo HITL).
- Generar el JWT de desarrollo desde la propia interfaz según el perfil.

La consola solo habla con la API REST; no importa código del backend.
"""
from __future__ import annotations

import base64
import json
import os
from datetime import date, datetime, timezone

import requests
import streamlit as st

BASE = os.getenv("FINPILOT_API_URL", "http://127.0.0.1:8000/api/v1").rstrip("/")
ROOT = BASE.removesuffix("/api/v1")
REQUEST_TIMEOUT = int(os.getenv("FINPILOT_REQUEST_TIMEOUT", "250"))

PROFILES = {
    "Consultar (operator)": "operator",
    "Aprobar (approver)": "approver",
    "Administrar (admin)": "admin",
}
ROLE_LABELS = {"ADMIN": "Administrador", "APPROVER": "Aprobador", "OPERATOR": "Consulta"}
STATUS_STYLES = {
    "COMPLETED": ("✅", "Completada", "success"),
    "AWAITING_APPROVAL": ("⏳", "Esperando aprobación", "warning"),
    "REJECTED": ("⛔", "Rechazada", "error"),
    "FAILED": ("❌", "Fallida", "error"),
    "EXECUTING": ("⚙️", "Ejecutando", "info"),
    "PLANNING": ("🧠", "Planificando", "info"),
    "RECEIVED": ("📥", "Recibida", "info"),
}

TEXT_EXAMPLES = {
    "¿Cuánto dinero hay en la cuenta de ahorro 1?": "¿Cuánto dinero hay en la cuenta de ahorro 1?",
    "Datos del cliente 1 y sus cuentas": "Muéstrame los datos del cliente 1 y el resumen de sus cuentas.",
    "Buscar clientes FinPilot": "Busca los clientes que se llamen FinPilot.",
    "Transacciones de la cuenta 1": "Consulta las últimas transacciones de la cuenta de ahorro 1.",
    "Transferir 10 USD de la cuenta 1 a la 2": (
        "Transfiere 10 dólares de la cuenta de ahorro 1 a la cuenta de ahorro 2."
    ),
}

# Planes de demostración: no requieren ningún modelo.
PLAN_EXAMPLES: dict[str, dict] = {
    "Consulta cuenta de ahorro 1": {
        "steps": [
            {
                "tool_name": "get_savings_account",
                "arguments": {"savingsId": 1},
                "justification": "Consulta de solo lectura para demo.",
            }
        ]
    },
    "Consulta cliente 1 y sus cuentas": {
        "steps": [
            {
                "tool_name": "get_client",
                "arguments": {"clientId": 1},
                "justification": "Consulta de solo lectura para demo.",
            },
            {
                "tool_name": "get_client_accounts",
                "arguments": {"clientId": 1},
                "justification": "Consulta de solo lectura para demo.",
            },
        ]
    },
    "Transferir 10 USD de cuenta 1 a 2 (HITL)": {
        "steps": [
            {
                "tool_name": "transfer_funds",
                "arguments": {
                    "fromAccountId": 1,
                    "toAccountId": 2,
                    "transactionDate": date.today().isoformat(),
                    "amount": 10,
                    "currency": "USD",
                    "description": "Transferencia de demo FinPilot",
                },
                "justification": "Requiere aprobación humana antes de ejecutarse.",
            }
        ]
    },
}


# ---------------------------------------------------------------------------
# Sesión y API
# ---------------------------------------------------------------------------
def _init_state() -> None:
    defaults = {
        "token": "",
        "username": "",
        "role": "",
        "last_operation": None,
        "last_error": None,
        "last_decision": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _headers() -> dict[str, str]:
    token = st.session_state.get("token", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


def _decode_claims(token: str) -> dict:
    """Decodifica el payload del JWT solo para mostrar usuario y expiración."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError, json.JSONDecodeError):
        return {}


def _error_detail(response: requests.Response) -> str:
    try:
        detail = response.json().get("detail")
        if detail:
            return str(detail)
    except ValueError:
        pass
    return response.text or f"HTTP {response.status_code}"


def _explain_error(status: int | None, detail: str) -> str:
    if status == 401:
        return "Necesitas iniciar sesión con un token válido (barra lateral)."
    if status == 403:
        return f"Tu usuario no tiene permiso para esta acción. {detail}"
    return detail


def _show_api_error(status: int | None, detail) -> None:
    message = _explain_error(status, str(detail))
    if status == 401:
        st.info(message)
    else:
        st.error(message)


def api_get(path: str, timeout: int = 15):
    try:
        response = requests.get(f"{BASE}{path}", headers=_headers(), timeout=timeout)
    except requests.RequestException as exc:
        return False, f"No se pudo contactar la API: {exc}", None
    if response.ok:
        try:
            return True, response.json(), response.status_code
        except ValueError:
            return True, response.text, response.status_code
    return False, _error_detail(response), response.status_code


def api_post(path: str, body: dict, timeout: int = REQUEST_TIMEOUT):
    try:
        response = requests.post(
            f"{BASE}{path}", json=body, headers=_headers(), timeout=timeout
        )
    except requests.RequestException as exc:
        return False, f"No se pudo contactar la API: {exc}", None
    if response.ok:
        try:
            return True, response.json(), response.status_code
        except ValueError:
            return True, response.text, response.status_code
    return False, _error_detail(response), response.status_code


def _apply_token(token: str, username: str = "", role: str = "") -> None:
    claims = _decode_claims(token)
    st.session_state.token = token
    st.session_state.username = username or claims.get("sub", "")
    st.session_state.role = role or claims.get("role", "")


def _issue_token(username: str) -> None:
    ok, data, status = api_post("/dev/token", {"username": username}, timeout=15)
    if ok and isinstance(data, dict):
        _apply_token(data["access_token"], data.get("username", username), data.get("role", ""))
        st.rerun()
    elif status == 404:
        st.warning(
            "El backend tiene `DEV_TOKEN_ENABLED=false`. Genera el token con "
            "`python scripts/issue_token.py operator` o habilita ese endpoint solo en local."
        )
    elif status == 503:
        _show_api_error(status, data)
    else:
        _show_api_error(status, data)


# ---------------------------------------------------------------------------
# Formato legible
# ---------------------------------------------------------------------------
def _currency(result: dict, default: str = "USD") -> str:
    currency = result.get("currency")
    if isinstance(currency, dict) and currency.get("code"):
        return str(currency["code"])
    return default


def _money(value, currency: str = "USD") -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):,.2f} {currency}"
    except (TypeError, ValueError):
        return str(value)


def _date(value) -> str:
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            return f"{int(value[2]):04d}-{int(value[1]):02d}-{int(value[0]):02d}"
        except (TypeError, ValueError):
            pass
    if isinstance(value, str) and value:
        return value[:10]
    return "—"


def _option(value) -> str:
    if isinstance(value, dict):
        return str(value.get("value") or value.get("code") or "—")
    return str(value or "—")


def _timestamp(value) -> str:
    if not value:
        return "—"
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return str(value)


def _status_text(status: str) -> str:
    icon, label, _ = STATUS_STYLES.get(status, ("ℹ️", status or "—", "info"))
    return f"{icon} {label}"


def _infer_tool(result) -> str | None:
    if not isinstance(result, dict):
        return None
    if "clients" in result:
        return "search_clients"
    if "loanAccounts" in result:
        return "get_client_accounts"
    if "savingsAccounts" in result:
        return "list_savings_accounts"
    if "loans" in result:
        return "list_loans"
    if "transactions" in result:
        return "transactions"
    if "summary" in result and "accountNo" in result:
        return "get_savings_account"
    if "loanProductName" in result:
        return "get_loan"
    if "displayName" in result:
        return "get_client"
    if "resourceId" in result:
        return "transfer_funds"
    return None


def _step_arguments(plan: dict | None, order) -> dict:
    """Devuelve los argumentos del paso del plan que corresponde al orden dado."""
    steps = (plan or {}).get("steps") if isinstance(plan, dict) else None
    if not isinstance(steps, list) or not steps:
        return {}
    if isinstance(order, int) and 1 <= order <= len(steps):
        return steps[order - 1].get("arguments") or {}
    if len(steps) == 1:
        return steps[0].get("arguments") or {}
    return {}


def describe_result(tool: str | None, result, arguments: dict | None = None) -> list[str]:
    """Convierte el resultado técnico de una tool en frases para el usuario."""
    if not isinstance(result, dict):
        return []
    tool = tool or _infer_tool(result)
    arguments = arguments or {}
    currency = _currency(result)

    if tool == "get_savings_account":
        summary = result.get("summary") or {}
        lines = [
            f"La **cuenta de ahorro #{result.get('id')}** de "
            f"**{result.get('clientName') or 'el cliente'}** tiene un saldo de "
            f"**{_money(summary.get('accountBalance'), currency)}**."
        ]
        if summary.get("availableBalance") is not None:
            lines.append(
                f"Saldo disponible: **{_money(summary.get('availableBalance'), currency)}**."
            )
        lines.append(
            f"Producto: {result.get('savingsProductName') or '—'} · "
            f"Estado: {_option(result.get('status'))} · "
            f"N.º de cuenta: {result.get('accountNo') or '—'}."
        )
        return lines

    if tool == "get_client":
        return [
            f"**{result.get('displayName') or 'Cliente'}** (ID {result.get('id')}) está "
            f"{'activo' if result.get('active') else 'inactivo'} en la oficina "
            f"{result.get('officeName') or '—'}."
        ]

    if tool == "search_clients":
        clients = result.get("clients") or []
        total = result.get("totalFilteredRecords", len(clients))
        if not clients:
            return ["No encontré clientes con ese criterio."]
        return [f"Encontré **{total}** cliente(s):"]

    if tool == "get_client_accounts":
        savings = result.get("savingsAccounts") or []
        loans = result.get("loanAccounts") or []
        return [
            f"El cliente tiene **{len(savings)}** cuenta(s) de ahorro y "
            f"**{len(loans)}** préstamo(s)."
        ]

    if tool == "list_savings_accounts":
        accounts = result.get("savingsAccounts") or []
        return [f"Hay **{result.get('totalFilteredRecords', len(accounts))}** cuenta(s) de ahorro."]

    if tool == "get_loan":
        return [
            f"El **préstamo #{result.get('id')}** de "
            f"**{result.get('clientName') or 'el cliente'}** es del producto "
            f"{result.get('loanProductName') or '—'} y está en estado "
            f"{_option(result.get('status'))}."
        ]

    if tool == "list_loans":
        loans = result.get("loans") or []
        return [f"Hay **{result.get('totalFilteredRecords', len(loans))}** préstamo(s)."]

    if tool in {"transactions", "list_client_transactions", "search_savings_transactions", "list_loan_transactions"}:
        transactions = result.get("transactions") or []
        return [
            f"Hay **{result.get('totalFilteredRecords', len(transactions))}** transacción(es)."
        ]

    if tool in {"get_savings_transaction", "get_loan_transaction"}:
        kind = result.get("transactionType") or result.get("type")
        reversed_flag = result.get("reversed", result.get("manuallyReversed"))
        return [
            f"La **transacción #{result.get('id')}** del {_date(result.get('date'))} "
            f"es de tipo {_option(kind)} por **{_money(result.get('amount'), currency)}**"
            f"{' (reversada)' if reversed_flag else ''}."
        ]

    if tool == "transfer_funds":
        source = arguments.get("fromAccountId")
        target = arguments.get("toAccountId")
        amount = arguments.get("amount")
        if source is not None and target is not None and amount is not None:
            currency = arguments.get("currency") or currency
            return [
                f"✅ Se transfirieron **{_money(amount, currency)}** de la cuenta "
                f"**#{source}** a la **#{target}** "
                f"(operación **#{result.get('resourceId')}**)."
            ]
        return [
            "✅ Transferencia registrada en Fineract. "
            f"Operación **#{result.get('resourceId')}** sobre la cuenta "
            f"**#{result.get('savingsId')}**."
        ]

    if tool == "update_fraud_status":
        return [
            f"La transacción **{result.get('transactionId')}** quedó marcada como "
            f"**{result.get('status')}**. Motivo: {result.get('reason') or '—'}."
        ]

    return []


def result_table(tool: str | None, result) -> list[dict]:
    """Devuelve filas tabulares para resultados de listado (opcional)."""
    if not isinstance(result, dict):
        return []
    tool = tool or _infer_tool(result)

    if tool == "search_clients":
        return [
            {
                "ID": client.get("id"),
                "Cliente": client.get("displayName") or "—",
                "Activo": "Sí" if client.get("active") else "No",
                "Oficina": client.get("officeName") or "—",
            }
            for client in result.get("clients") or []
        ]

    if tool == "get_client_accounts":
        rows = []
        for kind, key in (("Ahorro", "savingsAccounts"), ("Préstamo", "loanAccounts")):
            for account in result.get(key) or []:
                rows.append(
                    {
                        "Tipo": kind,
                        "ID": account.get("id"),
                        "Producto": account.get("productName") or "—",
                        "Estado": _option(account.get("status")),
                    }
                )
        return rows

    if tool == "list_savings_accounts":
        return [
            {
                "ID": account.get("id"),
                "Cliente": account.get("clientName") or "—",
                "Producto": account.get("savingsProductName") or "—",
                "Saldo": _money((account.get("summary") or {}).get("accountBalance"), _currency(account)),
                "Estado": _option(account.get("status")),
            }
            for account in result.get("savingsAccounts") or []
        ]

    if tool == "list_loans":
        return [
            {
                "ID": loan.get("id"),
                "Cliente": loan.get("clientName") or "—",
                "Producto": loan.get("loanProductName") or "—",
                "Estado": _option(loan.get("status")),
            }
            for loan in result.get("loans") or []
        ]

    if tool in {"transactions", "list_client_transactions", "search_savings_transactions", "list_loan_transactions"}:
        return [
            {
                "ID": transaction.get("id"),
                "Fecha": _date(transaction.get("date")),
                "Tipo": _option(transaction.get("transactionType") or transaction.get("type")),
                "Monto": _money(transaction.get("amount"), _currency(transaction)),
                "Reversada": "Sí" if transaction.get("reversed") else "No",
            }
            for transaction in result.get("transactions") or []
        ]

    return []


def _approval_title(tool: str | None, arguments: dict) -> str:
    if tool == "transfer_funds":
        return (
            f"Transferir **{_money(arguments.get('amount'), str(arguments.get('currency') or 'USD'))}** "
            f"de la cuenta **#{arguments.get('fromAccountId')}** a la "
            f"**#{arguments.get('toAccountId')}**."
        )
    if tool == "update_fraud_status":
        return (
            f"Marcar la transacción **{arguments.get('transactionId')}** como "
            f"**{arguments.get('status')}** (motivo: {arguments.get('reason') or '—'})."
        )
    return f"Ejecutar `{tool}` con `{json.dumps(arguments, ensure_ascii=False)}`."


def _render_status(status: str) -> None:
    icon, label, kind = STATUS_STYLES.get(status, ("ℹ️", status or "—", "info"))
    message = f"{icon} **{label}**"
    if kind == "success":
        st.success(message)
    elif kind == "warning":
        st.warning(message)
    elif kind == "error":
        st.error(message)
    else:
        st.info(message)


def render_step(step: dict, arguments: dict | None = None) -> None:
    tool = step.get("tool_name") or _infer_tool(step.get("result"))
    status = step.get("status", "")
    with st.container(border=True):
        st.markdown(
            f"**Paso {step.get('step', '·')}** · `{tool or 'herramienta'}` · {_status_text(status)}"
        )
        if status == "COMPLETED":
            result = step.get("result")
            lines = describe_result(tool, result, arguments)
            for line in lines:
                st.markdown(line)
            if not lines:
                st.markdown("La operación se ejecutó correctamente.")
            rows = result_table(tool, result)
            if rows:
                st.dataframe(rows, hide_index=True)
            if tool == "get_savings_account" and isinstance(result, dict):
                summary = result.get("summary") or {}
                if summary.get("accountBalance") is not None or summary.get("availableBalance") is not None:
                    currency = _currency(result)
                    left, right = st.columns(2)
                    left.metric("Saldo", _money(summary.get("accountBalance"), currency))
                    right.metric("Disponible", _money(summary.get("availableBalance"), currency))
        elif status == "AWAITING_APPROVAL":
            if arguments:
                st.markdown(_approval_title(tool, arguments))
            st.markdown(
                f"Esta operación necesita aprobación humana. "
                f"**Aprobación #{step.get('approval_id')}** · riesgo `{step.get('risk')}`."
            )
            st.caption("Ve a la pestaña **Aprobaciones** para aceptarla o rechazarla.")
        elif status == "REJECTED":
            if arguments:
                st.markdown(_approval_title(tool, arguments))
            st.markdown(
                "La operación fue **rechazada**; no se ejecutó ningún cambio en Fineract."
            )
        elif status == "FAILED":
            st.markdown("El paso falló. Revisa el JSON para ver el detalle.")


def render_operation(payload: dict) -> None:
    _render_status(str(payload.get("status", "")))
    st.caption(f"Solicitud **#{payload.get('request_id')}**")
    plan = payload.get("plan")
    for step in payload.get("steps") or []:
        render_step(step, _step_arguments(plan, step.get("step")))
    with st.expander("🔍 Ver plan y JSON completo"):
        if plan:
            st.caption("Plan propuesto por el agente")
            st.json(plan)
        else:
            st.caption("La API no devolvió el plan (respuesta de una versión anterior).")
        st.caption("Respuesta completa")
        st.json(payload)
    if payload.get("trace_id"):
        st.caption(f"LangSmith · trace `{payload['trace_id']}`")


def render_history_item(item: dict) -> None:
    with st.container(border=True):
        st.markdown(f"{_status_text(str(item.get('status', '')))} · {_timestamp(item.get('created_at'))}")
        st.caption(f"Instrucción: {item.get('instruction') or '—'}")
        result = item.get("result")
        plan = item.get("plan")
        if isinstance(result, dict):
            if isinstance(result.get("steps"), list):
                for step in result["steps"]:
                    render_step(step, _step_arguments(plan, step.get("step")))
            elif result.get("tool_name"):
                tool = result.get("tool_name")
                arguments = _step_arguments(plan, 1)
                for line in describe_result(tool, result.get("result"), arguments):
                    st.markdown(line)
                rows = result_table(tool, result.get("result"))
                if rows:
                    st.dataframe(rows, hide_index=True)
            elif result:
                st.caption("Resultado registrado; consulta el JSON.")
        if plan:
            with st.expander("Ver plan"):
                st.json(item["plan"])
        with st.expander("Ver JSON completo"):
            st.json(item)


def render_decision(record: dict) -> None:
    """Resumen persistente de la última aprobación o rechazo desde el dashboard."""
    approved = record.get("decision") == "APPROVED"
    execution = record.get("execution") or {}
    with st.container(border=True):
        if approved:
            st.success(
                f"✅ Aprobación #{record.get('approval_id')} ejecutada correctamente. "
                "La operación quedó registrada en Fineract."
            )
        else:
            st.warning(
                f"⛔ Aprobación #{record.get('approval_id')} rechazada. "
                "No se ejecutó ningún cambio en Fineract."
            )
        if execution:
            st.caption(f"Solicitud #{execution.get('request_id')}")
            plan = execution.get("plan")
            for step in execution.get("steps") or []:
                render_step(step, _step_arguments(plan, step.get("step")))
        st.caption(
            "El resultado también quedó disponible en las pestañas **Operar** e **Historial**."
        )
        hide, _ = st.columns([1, 3])
        if hide.button("Ocultar resumen", key="hide_decision", width="stretch"):
            st.session_state.last_decision = None
            st.rerun()
        with st.expander("Ver JSON de la ejecución"):
            st.json(record)


def render_approval(item: dict) -> None:
    tool = item.get("tool_name")
    arguments = item.get("arguments") or {}
    with st.container(border=True):
        st.markdown(_approval_title(tool, arguments))
        left, right = st.columns([2, 1])
        left.caption(
            f"Aprobación **#{item.get('id')}** · solicitud **#{item.get('request_id')}** "
            f"· hash `{str(item.get('payload_hash') or '')[:16]}…`"
        )
        right.caption(f"Expira: {_timestamp(item.get('expires_at'))}")
        with st.expander("Ver argumentos (JSON)"):
            st.json({"tool": tool, "arguments": arguments, "payload_hash": item.get("payload_hash")})
        decision = st.radio(
            "Decisión",
            ["Aprobar", "Rechazar"],
            horizontal=True,
            key=f"decision_{item.get('id')}",
        )
        if st.button("Confirmar decisión", key=f"confirm_{item.get('id')}", type="primary"):
            body = {"decision": "APPROVED" if decision == "Aprobar" else "REJECTED"}
            ok, data, status = api_post(f"/approvals/{item.get('id')}/decide", body)
            if ok:
                payload = data if isinstance(data, dict) else {}
                execution = payload.get("execution")
                st.session_state.last_decision = {
                    "approval_id": item.get("id"),
                    "decision": body["decision"],
                    "tool_name": tool,
                    "execution": execution,
                }
                if body["decision"] == "APPROVED" and execution:
                    # Mantiene el resultado completo visible en la pestaña Operar.
                    st.session_state.last_operation = execution
                elif body["decision"] == "REJECTED":
                    st.session_state.last_operation = {
                        "request_id": item.get("request_id"),
                        "status": "REJECTED",
                        "steps": [
                            {
                                "step": item.get("step", 1),
                                "status": "REJECTED",
                                "tool_name": tool,
                                "approval_id": item.get("id"),
                            }
                        ],
                    }
                st.rerun()
            else:
                _show_api_error(status, data)


# ---------------------------------------------------------------------------
# Interfaz
# ---------------------------------------------------------------------------
st.set_page_config(page_title="FinPilot", page_icon="💸", layout="wide")
_init_state()

st.title("FinPilot · Operaciones")
st.caption(
    "Agente de operaciones sobre un sandbox de Apache Fineract. "
    "Entorno de desarrollo: no usar con datos reales."
)

with st.sidebar:
    st.header("Sesión")
    if st.session_state.token:
        claims = _decode_claims(st.session_state.token)
        username = st.session_state.username or claims.get("sub", "usuario")
        role = st.session_state.role or claims.get("role", "")
        st.success(f"{username} · {ROLE_LABELS.get(role, role or 'sin rol')}")
        if claims.get("exp"):
            st.caption(f"Token válido hasta {_timestamp(datetime.fromtimestamp(int(claims['exp']), tz=timezone.utc))}")
        if st.button("Cerrar sesión", width="stretch"):
            for key in ("token", "username", "role", "last_operation", "last_error", "last_decision"):
                st.session_state[key] = "" if key in {"token", "username", "role"} else None
            st.rerun()
    else:
        st.caption("Elige un perfil y genera el token de desarrollo.")
        profile = st.selectbox("Perfil", list(PROFILES), key="login_profile")
        if st.button("🔑 Generar token", type="primary", width="stretch"):
            _issue_token(PROFILES[profile])
        with st.expander("Pegar un token manualmente"):
            manual = st.text_input("Token JWT", type="password", key="manual_token")
            if st.button("Usar token", key="use_manual_token"):
                if manual.strip():
                    _apply_token(manual.strip())
                    st.rerun()
                else:
                    st.warning("Pega un token válido.")
        st.caption(
            "El botón usa `POST /api/v1/dev/token`, disponible solo con "
            "`DEV_TOKEN_ENABLED=true` en el backend local."
        )
    st.divider()
    try:
        health = requests.get(f"{ROOT}/health", timeout=3)
        if health.ok:
            st.caption("🟢 API conectada")
        else:
            st.caption(f"🔴 API no disponible (HTTP {health.status_code})")
    except requests.RequestException:
        st.caption("🔴 API no disponible")
    st.divider()
    st.caption(
        "El planificador usa el modelo configurado en el backend (Ollama, "
        "Hugging Face o Watsonx). Sin modelo, utiliza el modo de plan estructurado."
    )

tab_ops, tab_approvals, tab_history = st.tabs(["💬 Operar", "✅ Aprobaciones", "🕘 Historial"])

with tab_ops:
    if not st.session_state.token:
        st.info(
            "Sin sesión: si el backend tiene `AUTH_DISABLED=false`, genera un token "
            "en la barra lateral antes de enviar operaciones."
        )

    def _load_text_example() -> None:
        choice = st.session_state.get("text_example", "—")
        if choice in TEXT_EXAMPLES:
            st.session_state.instruction_text = TEXT_EXAMPLES[choice]

    st.selectbox(
        "Ejemplos rápidos",
        ["—"] + list(TEXT_EXAMPLES),
        key="text_example",
        on_change=_load_text_example,
    )
    instruction = st.text_area(
        "¿Qué quieres hacer?",
        key="instruction_text",
        height=90,
        placeholder="Ej.: ¿Cuánto dinero hay en la cuenta de ahorro 1?",
    )
    mode = st.radio(
        "Modo",
        ["Lenguaje natural (modelo)", "Plan estructurado (sin modelo)"],
        horizontal=True,
        key="input_mode",
    )
    structured = mode.startswith("Plan")

    plan_payload = None
    if structured:
        if "plan_text" not in st.session_state:
            st.session_state.plan_text = json.dumps(
                PLAN_EXAMPLES[next(iter(PLAN_EXAMPLES))], indent=2, ensure_ascii=False
            )

        def _load_plan_example() -> None:
            choice = st.session_state.get("plan_example", next(iter(PLAN_EXAMPLES)))
            st.session_state.plan_text = json.dumps(
                PLAN_EXAMPLES[choice], indent=2, ensure_ascii=False
            )

        st.selectbox(
            "Plan de ejemplo",
            list(PLAN_EXAMPLES),
            key="plan_example",
            on_change=_load_plan_example,
        )
        raw_plan = st.text_area("Plan JSON (editable)", key="plan_text", height=220)
        try:
            plan_payload = json.loads(raw_plan)
        except json.JSONDecodeError as exc:
            st.error(f"El plan no es JSON válido: {exc}")
            plan_payload = None

    send_col, clear_col = st.columns(2)
    send = send_col.button("Enviar operación", type="primary", width="stretch")
    if clear_col.button("Limpiar resultado", width="stretch"):
        st.session_state.last_operation = None
        st.session_state.last_error = None

    if send:
        text = instruction.strip()
        if not text and not structured:
            st.warning("Escribe una instrucción o elige un ejemplo.")
        elif structured and plan_payload is None:
            st.warning("Corrige el JSON del plan antes de enviar.")
        else:
            body: dict = {"instruction": text or "Operación de demostración (plan estructurado)"}
            if structured:
                body["plan"] = plan_payload
            ok, data, status = api_post("/ops", body)
            if ok:
                st.session_state.last_operation = data
                st.session_state.last_error = None
                st.session_state.last_decision = None
            else:
                st.session_state.last_operation = None
                st.session_state.last_error = {"status": status, "detail": str(data)}

    if st.session_state.last_error:
        error = st.session_state.last_error
        _show_api_error(error.get("status"), error.get("detail"))
    if st.session_state.last_operation:
        render_operation(st.session_state.last_operation)

with tab_approvals:
    if st.session_state.last_decision:
        render_decision(st.session_state.last_decision)
    ok, data, status = api_get("/approvals")
    if not ok:
        _show_api_error(status, data)
    elif not data:
        st.info(
            "No hay aprobaciones pendientes. Las operaciones de escritura aparecen "
            "aquí hasta que un usuario con rol aprobador las acepte o rechace."
        )
    else:
        st.markdown(f"Hay **{len(data)}** solicitud(es) esperando decisión.")
        for approval in data:
            render_approval(approval)

with tab_history:
    ok, data, status = api_get("/ops")
    if not ok:
        _show_api_error(status, data)
    elif not data:
        st.info("Todavía no hay operaciones registradas.")
    else:
        choices = {
            f"#{item.get('id')} · {_status_text(str(item.get('status', '')))} · "
            f"{str(item.get('instruction') or '')[:70]}": item
            for item in data
        }
        selected = st.selectbox("Solicitud", list(choices), key="history_pick")
        render_history_item(choices[selected])
