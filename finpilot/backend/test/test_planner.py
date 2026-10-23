"""Pruebas unitarias del planificador (sin red ni modelo real)."""
from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.agent.planner import (
    PlannerConfigurationError,
    _calls_to_plan,
    _parse_plan,
    plan_instruction,
)
from app.core.config import Settings


def test_parse_plan_accepts_fenced_json() -> None:
    plan = _parse_plan('```json\n{"steps":[{"tool_name":"get_client","arguments":{"clientId":1}}]}\n```')
    assert plan["steps"][0]["tool_name"] == "get_client"


def test_parse_plan_extracts_json_from_text() -> None:
    plan = _parse_plan('Claro:\n{"steps":[{"tool_name":"get_loan","arguments":{"loanId":2}}]}')
    assert plan["steps"][0]["arguments"] == {"loanId": 2}


def test_parse_plan_rejects_non_json() -> None:
    with pytest.raises(PlannerConfigurationError):
        _parse_plan("no hay plan")


def test_calls_to_plan_strips_approval_metadata() -> None:
    plan = _calls_to_plan(
        [
            {
                "name": "transfer_funds",
                "args": {"fromAccountId": 1, "approvalId": 9, "payloadHash": "x"},
            }
        ]
    )
    assert plan["steps"][0]["arguments"] == {"fromAccountId": 1}


async def test_plan_instruction_requires_hf_token(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        model_provider="huggingface",
        hf_token=SecretStr(""),
        mcp_enabled=True,
    )
    monkeypatch.setattr("app.agent.planner.get_settings", lambda: settings)
    with pytest.raises(PlannerConfigurationError, match="Hugging Face no está configurado"):
        await plan_instruction("consulta la cuenta 1")
