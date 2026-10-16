"""Consistencia entre los contratos de la app y el harness de evaluación.

El harness (``scripts/eval_tool_calling.py``) es autónomo y no importa
``app/``. Esta prueba lo carga con ``importlib`` para impedir que los
contratos de herramientas se separen: mismos nombres, mismas restricciones de
argumentos, mismos efectos (solo lectura).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from pydantic import BaseModel

from app.tools import registry
from app.tools.schemas import (
    GetClientAccountsArgs,
    GetClientArgs,
    GetLoanArgs,
    GetLoanTransactionArgs,
    GetSavingsAccountArgs,
    GetSavingsTransactionArgs,
    ListClientTransactionsArgs,
    ListLoansArgs,
    ListLoanTransactionsArgs,
    ListSavingsAccountsArgs,
    SearchClientsArgs,
    SearchSavingsTransactionsArgs,
)

HARNESS_PATH = Path(__file__).resolve().parents[1] / "scripts" / "eval_tool_calling.py"

APP_ARG_MODELS: dict[str, type[BaseModel]] = {
    "search_clients": SearchClientsArgs,
    "get_client": GetClientArgs,
    "get_client_accounts": GetClientAccountsArgs,
    "get_savings_account": GetSavingsAccountArgs,
    "get_loan": GetLoanArgs,
    "list_savings_accounts": ListSavingsAccountsArgs,
    "list_loans": ListLoansArgs,
    "list_client_transactions": ListClientTransactionsArgs,
    "search_savings_transactions": SearchSavingsTransactionsArgs,
    "list_loan_transactions": ListLoanTransactionsArgs,
    "get_savings_transaction": GetSavingsTransactionArgs,
    "get_loan_transaction": GetLoanTransactionArgs,
}


@pytest.fixture(scope="module")
def harness() -> ModuleType:
    spec = importlib.util.spec_from_file_location("finpilot_eval_harness", HARNESS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _non_null_types(fragment: dict) -> set[str]:
    options = fragment.get("anyOf")
    if options is None:
        return {fragment["type"]}
    return {option["type"] for option in options if option.get("type") != "null"}


def _constraints(fragment: dict) -> dict:
    branch = fragment
    options = fragment.get("anyOf")
    if options:
        branch = next(option for option in options if option.get("type") != "null")
    return {key: branch[key] for key in ("minimum", "maximum") if key in branch}


def test_tool_name_sets_match(harness: ModuleType) -> None:
    assert registry.tool_names() == frozenset(harness.TOOL_BY_NAME)
    assert set(APP_ARG_MODELS) == frozenset(harness.TOOL_BY_NAME)


def test_harness_exposes_only_read_tools(harness: ModuleType) -> None:
    for tool in harness.READ_TOOLS:
        assert tool.side_effect == "read"
        assert tool.name not in harness.WRITE_TOOL_NAMES


def test_app_registry_exposes_only_read_tools(harness: ModuleType) -> None:
    for name in registry.tool_names():
        assert name not in harness.WRITE_TOOL_NAMES


def _without_descriptions(node):
    if isinstance(node, dict):
        return {
            key: _without_descriptions(value)
            for key, value in node.items()
            if key != "description"
        }
    if isinstance(node, list):
        return [_without_descriptions(item) for item in node]
    return node


@pytest.mark.parametrize("name", sorted(APP_ARG_MODELS))
def test_argument_models_are_equivalent(harness: ModuleType, name: str) -> None:
    app_model = APP_ARG_MODELS[name]
    harness_model: type[BaseModel] = harness.TOOL_BY_NAME[name].args_model

    app_schema = _without_descriptions(app_model.model_json_schema(by_alias=True))
    harness_schema = _without_descriptions(harness_model.model_json_schema(by_alias=True))
    assert app_schema == harness_schema


@pytest.mark.parametrize("name", sorted(APP_ARG_MODELS))
def test_app_schema_matches_harness_parameters(harness: ModuleType, name: str) -> None:
    app_schema = APP_ARG_MODELS[name].model_json_schema(by_alias=True)
    harness_parameters = harness.TOOL_BY_NAME[name].parameters

    app_props = app_schema["properties"]
    harness_props = harness_parameters["properties"]
    assert set(app_props) == set(harness_props)

    for prop in app_props:
        assert _non_null_types(app_props[prop]) == _non_null_types(harness_props[prop])
        assert _constraints(app_props[prop]) == _constraints(harness_props[prop])

    assert set(app_schema.get("required", [])) == set(harness_parameters.get("required", []))
    assert app_schema.get("additionalProperties") is False
    assert harness_parameters.get("additionalProperties") is False
