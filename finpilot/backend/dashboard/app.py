import os
import requests
import streamlit as st

BASE = os.getenv("FINPILOT_API_URL", "http://127.0.0.1:8000/api/v1")
st.title("FinPilot · Operaciones")
token = st.text_input("Bearer token (opcional en modo local)", type="password")
headers = {"Authorization": f"Bearer {token}"} if token else {}
instruction = st.text_area("Instrucción")
if st.button("Enviar") and instruction:
    try:
        response = requests.post(f"{BASE}/ops", json={"instruction": instruction}, headers=headers, timeout=60)
        st.json(response.json())
    except Exception as exc:
        st.error(str(exc))
st.subheader("Aprobaciones pendientes")
try:
    pending = requests.get(f"{BASE}/approvals", headers=headers, timeout=10).json()
    for item in pending:
        st.write(item)
        choice = st.selectbox("Decisión", ["APPROVED", "REJECTED"], key=f"decision-{item['id']}")
        if st.button("Aplicar decisión", key=f"apply-{item['id']}"):
            st.json(requests.post(f"{BASE}/approvals/{item['id']}/decide", json={"decision": choice}, headers=headers).json())
except Exception as exc:
    st.info(f"API no disponible: {exc}")
st.subheader("Historial")
try:
    st.json(requests.get(f"{BASE}/ops", headers=headers, timeout=10).json())
except Exception as exc:
    st.info(f"API no disponible: {exc}")
