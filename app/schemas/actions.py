from typing import Literal

from pydantic import BaseModel, Field


class CreateTicketRequest(BaseModel):
    conversation_id: int
    description: str = Field(min_length=1)
    ticket_type: Literal["用电业务", "投诉", "咨询"]


class CreateTicketResponse(BaseModel):
    ticket_no: str
    status: str = "已转人工"


class CreateRefundRequest(BaseModel):
    conversation_id: int
    workorder_no: str = Field(min_length=1)
    reason: Literal["七天无理由", "计量故障", "发错货", "不想要了", "其他"]


class CreateRefundResponse(BaseModel):
    ticket_no: str
    status: str = "退费申请已提交"


class ResumeRequest(BaseModel):
    conversation_id: int
    workorder_no: str | None = Field(default=None, min_length=1)   # ticket 工单选择器点选
    confirmed: bool | None = None                              # interrupt 工单预览 确认/取消
