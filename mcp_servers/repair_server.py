"""报修 MCP Server(interrupt 自建,mock 数据,不接真实系统、不建表)。
独立进程:uv run python mcp_servers/repair_server.py
验收 6:MOCK_DELAY_SECONDS=12 起进程 → 每次工具调用慢 12 秒,客服侧看超时/重试/审计。
实装 mcp==1.28.1:FastMCP(name, host=, port=) + run(transport="streamable-http"),路径默认 /mcp。"""
import asyncio
import os
import random
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

mcp = FastMCP("repair",
              host="127.0.0.1", port=int(os.environ.get("PORT", "8101")))

_DELAY = float(os.environ.get("MOCK_DELAY_SECONDS", "0"))
_STATUS_CODES = ["ACCEPTED", "DISPATCHING", "ON_SITE", "RESTORED"]  # 内部枚举码,client 侧翻人话
_SITES = ["城区供电所", "城南抢修基地", "10kV 滨江线现场", "城东营业站"]


@mcp.tool()
async def query_repair(
    repair_no: Annotated[str, Field(description="报修单号(形如 RX 开头),需先用 query_workorder 查工单拿到该单号")],
) -> dict:
    """用报修单号(repair_no)查询停电报修状态、当前位置和抢修进度。用于用户询问报修/抢修到哪了时。
    报修单号不是工单号,需先用 query_workorder 查工单拿到 repair_no,再调用本工具。"""
    if _DELAY > 0:
        await asyncio.sleep(_DELAY)
    rng = random.Random(f"repair:{repair_no}")         # 种子固定 → 同单号稳定
    code = rng.choice(_STATUS_CODES)
    site = rng.choice(_SITES)
    return {
        "repair_no": repair_no,
        "status_code": code,                              # 内部枚举码,Server 侧不翻译,交 client 侧治理
        "current_site": site,
        "trace": [f"抢修队已从{site}出发", f"内部状态码:{code}"],
        "crew_code": "CREW-07",                           # 内部抢修班组编码(回答用不上,client 侧应剔除)
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
