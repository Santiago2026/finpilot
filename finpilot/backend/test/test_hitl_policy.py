import pytest
from app.approvals.service import payload_hash
from app.policies.engine import classify_risk, validate_tool_call

def test_read_tools_are_low_risk():
    assert classify_risk("get_client")[0] == "LOW"

def test_unknown_or_write_tool_is_high_risk():
    assert classify_risk("transfer_funds")[0] == "HIGH_SENSITIVE"

def test_tool_arguments_reject_unknown_fields():
    with pytest.raises(Exception):
        validate_tool_call("get_client", {"clientId": 1, "extra": True})

def test_payload_hash_is_stable_and_bound_to_tool_and_arguments():
    args = {"clientId": 4}
    assert payload_hash("transfer_funds", args) == payload_hash("transfer_funds", {"clientId": 4})
    assert payload_hash("transfer_funds", args) != payload_hash("update_fraud_status", args)
    assert payload_hash("transfer_funds", args) != payload_hash("transfer_funds", {"clientId": 5})
