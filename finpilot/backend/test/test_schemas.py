from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.tools.schemas import (
    ClientSummary,
    GetClientArgs,
    SearchClientsArgs,
    SearchClientsResult,
)


class TestSearchClientsArgs:
    def test_accepts_alias_and_canonicalizes(self) -> None:
        args = SearchClientsArgs.model_validate({"displayName": "Ana", "limit": 5})
        assert args.model_dump(by_alias=True) == {"displayName": "Ana", "limit": 5}

    def test_accepts_pythonic_names_via_populate_by_name(self) -> None:
        args = SearchClientsArgs.model_validate({"display_name": "Ana"})
        assert args.display_name == "Ana"

    def test_all_fields_optional(self) -> None:
        args = SearchClientsArgs.model_validate({})
        assert args.display_name is None and args.limit is None

    @pytest.mark.parametrize("limit", [0, -1, 101, 1000])
    def test_limit_out_of_range_rejected(self, limit: int) -> None:
        with pytest.raises(ValidationError):
            SearchClientsArgs.model_validate({"limit": limit})

    @pytest.mark.parametrize("limit", [1, 100])
    def test_limit_bounds_accepted(self, limit: int) -> None:
        args = SearchClientsArgs.model_validate({"limit": limit})
        assert args.limit == limit

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            SearchClientsArgs.model_validate({"displayName": "Ana", "officeId": 1})

    def test_limit_type_coercion(self) -> None:
        args = SearchClientsArgs.model_validate({"limit": "5"})
        assert args.limit == 5

    def test_limit_non_numeric_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SearchClientsArgs.model_validate({"limit": "abc"})


class TestGetClientArgs:
    def test_requires_client_id(self) -> None:
        with pytest.raises(ValidationError):
            GetClientArgs.model_validate({})

    def test_client_id_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            GetClientArgs.model_validate({"clientId": 0})
        with pytest.raises(ValidationError):
            GetClientArgs.model_validate({"clientId": -3})

    def test_accepts_alias_and_canonicalizes(self) -> None:
        args = GetClientArgs.model_validate({"clientId": 42})
        assert args.model_dump(by_alias=True) == {"clientId": 42}

    def test_coerces_string_id(self) -> None:
        args = GetClientArgs.model_validate({"clientId": "7"})
        assert args.client_id == 7

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            GetClientArgs.model_validate({"clientId": 1, "includeAccounts": True})


class TestResponseModels:
    def test_client_summary_ignores_unknown_fields(self) -> None:
        summary = ClientSummary.model_validate(
            {"id": 1, "displayName": "Ana", "mobileNo": "600000000", "timeline": {}}
        )
        assert summary.id == 1
        assert summary.display_name == "Ana"

    def test_client_summary_requires_id(self) -> None:
        with pytest.raises(ValidationError):
            ClientSummary.model_validate({"displayName": "Ana"})

    def test_search_result_parses_alias(self) -> None:
        result = SearchClientsResult.model_validate(
            {
                "totalFilteredRecords": 2,
                "clients": [
                    {"id": 1, "displayName": "Ana", "active": True},
                    {"id": 2, "displayName": "Luis", "active": False, "officeName": "Oficina"},
                ],
            }
        )
        assert result.total_filtered_records == 2
        assert result.clients[1].office_name == "Oficina"

    def test_search_result_requires_total(self) -> None:
        with pytest.raises(ValidationError):
            SearchClientsResult.model_validate({"clients": []})
