import random
from typing import Annotated

from langchain_core.tools import InjectedToolArg, tool
from pydantic import Field

from app.tools import registry
from app.tools.business import workorder_snapshot, owns_workorder

# 查不到和不是本人的单回同一句话。分开回就成了枚举 oracle:攻击者靠回答差异
# 就能挨个试出哪些工单号真实存在。
NOT_OWNED = {"error": "没有找到您的这份工单", "code": "workorder_not_owned"}


@tool
async def query_workorder(
    workorder_no: Annotated[str, Field(description="工单号,例如 1001")],
    user_id: Annotated[str, InjectedToolArg],
) -> dict:
    """查询工单的状态、预估费用、受理时间、业务类型和关联单号(repair_no)。用于用户询问某个工单情况时。
    要查停电报修关联的抢修进度,需先用本工具拿到 repair_no,再把它传给 query_repair。
    发起人身份由系统注入,你不要传 user_id。"""
    if not owns_workorder(user_id, workorder_no):
        return dict(NOT_OWNED)
    return workorder_snapshot(workorder_no)


@tool
async def query_meter(
    model_name: Annotated[str, Field(description="电表/互感器型号或关键词,例如 DDZY666-Z")],
) -> dict:
    """查询电能表、互感器等计量设备的价格、备表数量和规格。用于用户咨询某型号有没有备表、多少钱时。"""
    rng = random.Random(f"meter:{model_name}")
    return {
        "model_name": model_name,
        "price": rng.randint(200, 2000),
        "stock": rng.randint(0, 500),
        "spec": rng.choice(["单相", "三相", "经互感器"]),
    }


registry.register(registry.spec_from_langchain_tool(
    query_workorder, source="builtin", inject_user_id=True))
registry.register(registry.spec_from_langchain_tool(query_meter, source="builtin"))
