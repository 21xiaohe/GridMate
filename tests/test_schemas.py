import pytest
from pydantic import ValidationError

from app.schemas.chat import ChatRequest
from app.schemas.extract import AfterSalesTicket, ExtractRequest, RequestType


def test_chat_request_rejects_empty_message():
    with pytest.raises(ValidationError):
        ChatRequest(session_id="s1", message="")


def test_extract_request_rejects_empty_text():
    with pytest.raises(ValidationError):
        ExtractRequest(text="")


def test_ticket_order_id_nullable():
    t = AfterSalesTicket(workorder_no=None, request_type="退费", expected_solution="退全款")
    assert t.workorder_no is None
    assert t.request_type is RequestType.REFUND


def test_ticket_rejects_unknown_request_type():
    with pytest.raises(ValidationError):
        AfterSalesTicket(workorder_no=None, request_type="砍价", expected_solution="x")


def test_ticket_normalizes_placeholder_order_id_to_none():
    for placeholder in ("null", "None", "无", " N/A ", ""):
        t = AfterSalesTicket(
            workorder_no=placeholder, request_type="投诉", expected_solution="x"
        )
        assert t.workorder_no is None, placeholder
    assert AfterSalesTicket(
        workorder_no="GD20260701123", request_type="退费", expected_solution="x"
    ).workorder_no == "GD20260701123"
