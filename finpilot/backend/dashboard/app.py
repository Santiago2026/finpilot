import json
import os

import requests
import streamlit as st

BASE = os.getenv("FINPILOT_API_URL", "http://127.0.0.1:8000/api/v1")

# Planes de demostración: no requieren Watsonx ni ningún LLM.
EXAMPLES: dict[str, dict] = {
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
                    "transactionDate": "2026-10-23",
                    "amount": 10,
                    "currency": "USD",
                    "description": "Transferencia de demo FinPilot",
                },
                "justification": "Requiere aprobación humana antes de ejecutarse.",
            }
        ]
    },
}

st.set_page_config(page_title="FinPilot", page_icon="💸", layout="wide")
st.title("FinPilot · Operaciones")
st.caption("Entorno de desarrollo sobre un sandbox de Apache Fineract. No usar con datos reales.")

with st.sidebar:
    st.header("Sesión")
    token = st.text_input("Bearer token", type="password")
    st.caption(
        "Genera uno con `python scripts/issue_token.py approver` "
        "o `python scripts/issue_token.py operator`."
    )
    st.divider()
    st.markdown(
        "**Sin modelo configurado** usa el plan estructurado. "
        "**Con Hugging Face (`HF_TOKEN`) o Watsonx** desmarca la casilla y escribe una instrucción libre."
    )

headers = {"Authorization": f"Bearer {token}"} if token else {}

st.subheader("Nueva operación")
instruction = st.text_area(
    "Instrucción (texto para el operador o para el LLM)",
    placeholder="Ej.: Consulta el saldo de la cuenta 1",
)

use_plan = st.checkbox(
    "Usar plan estructurado (no requiere Watsonx)",
    value=True,
    help="El backend acepta un plan ya construido y lo valida de forma determinística.",
)

plan_payload = None
if use_plan:
    if "plan_text" not in st.session_state:
        st.session_state.plan_text = json.dumps(EXAMPLES[next(iter(EXAMPLES))], indent=2, ensure_ascii=False)

    def _load_example() -> None:
        st.session_state.plan_text = json.dumps(
            EXAMPLES[st.session_state.example_name], indent=2, ensure_ascii=False
        )

    st.selectbox(
        "Plan de ejemplo",
        list(EXAMPLES),
        key="example_name",
        on_change=_load_example,
    )
    raw_plan = st.text_area("Plan JSON", key="plan_text", height=240)
    try:
        plan_payload = json.loads(raw_plan)
    except json.JSONDecodeError as exc:
        st.error(f"El plan no es JSON válido: {exc}")
        plan_payload = None

if st.button("Enviar", type="primary"):
    if not instruction.strip():
        st.warning("Escribe una instrucción.")
    elif use_plan and plan_payload is None:
        st.warning("Corrige el JSON del plan antes de enviar.")
    else:
        body: dict = {"instruction": instruction}
        if use_plan:
            body["plan"] = plan_payload
        try:
            response = requests.post(f"{BASE}/ops", json=body, headers=headers, timeout=60)
            if response.ok:
                st.success(f"HTTP {response.status_code}")
            else:
                st.error(f"HTTP {response.status_code}: {response.text}")
            try:
                st.json(response.json())
            except ValueError:
                st.code(response.text)
        except Exception as exc:
            st.error(f"No se pudo contactar la API: {exc}")

st.subheader("Aprobaciones pendientes")
try:
    response = requests.get(f"{BASE}/approvals", headers=headers, timeout=10)
    if response.ok:
        pending = response.json()
        if not pending:
            st.info("No hay aprobaciones pendientes.")
        for item in pending:
            with st.container(border=True):
                st.write(f"**Aprobación {item['id']}** · `{item['tool_name']}`")
                st.json(item.get("arguments", {}))
                st.caption(f"Expira: {item.get('expires_at')} · hash: `{item.get('payload_hash', '')[:16]}…`")
                choice = st.selectbox(
                    "Decisión",
                    ["APPROVED", "REJECTED"],
                    key=f"decision-{item['id']}",
                )
                if st.button("Aplicar decisión", key=f"apply-{item['id']}"):
                    decision = requests.post(
                        f"{BASE}/approvals/{item['id']}/decide",
                        json={"decision": choice},
                        headers=headers,
                        timeout=60,
                    )
                    if decision.ok:
                        st.success(f"HTTP {decision.status_code}")
                    else:
                        st.error(f"HTTP {decision.status_code}: {decision.text}")
                    st.json(decision.json())
    else:
        st.error(f"HTTP {response.status_code}: {response.text}")
except Exception as exc:
    st.info(f"API no disponible: {exc}")

st.subheader("Historial")
try:
    response = requests.get(f"{BASE}/ops", headers=headers, timeout=10)
    if response.ok:
        st.json(response.json())
    else:
        st.error(f"HTTP {response.status_code}: {response.text}")
except Exception as exc:
    st.info(f"API no disponible: {exc}")
