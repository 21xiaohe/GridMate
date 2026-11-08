"""interrupt MCP Client:MultiServerMCPClient 多 Server 接入,现问现拿(每次现拉工具清单,
adapters 每次调用新建 session——Server 侧加工具,客服系统不重启即可见)。
权限/格式化只认我们侧:Server 自报的用途描述仅供模型参考,能不能调按 registry.WRITE_TOOLS。"""
import asyncio
import logging

from langchain_mcp_adapters.client import MultiServerMCPClient

from app.config import settings
from app.tools import registry
from app.tools.registry import ToolSpec

logger = logging.getLogger(__name__)


def _translate(mapping: dict[str, str], code):
    return mapping.get(code, code)


def _fmt_repair(d: dict) -> dict:
    return {"repair_no": d.get("repair_no"),
            "status": _translate({"ACCEPTED": "已受理", "DISPATCHING": "抢修派单中",
                                  "ON_SITE": "现场处理中", "RESTORED": "已复电"}, d.get("status_code")),
            "current_site": d.get("current_site"), "trace": d.get("trace")}


def _fmt_warranty(d: dict) -> dict:
    return {"workorder_no": d.get("workorder_no"),
            "warranty": _translate({"IN_WARRANTY": "维护期内", "EXPIRED": "已出维护期"}, d.get("warranty_code")),
            "warranty_until": d.get("warranty_until")}


def _fmt_return(d: dict) -> dict:
    return {"workorder_no": d.get("workorder_no"),
            "return_status": _translate({"AUDITING": "审核中", "RETURNING": "退费中",
                                         "REFUNDED": "已退费", "NONE": "无退费记录"}, d.get("return_code")),
            "updated_at": d.get("updated_at")}


# 结果格式化我们侧登记(挑回答用得上的字段 + 内部枚举码翻人话);未登记的 MCP 工具透传
FORMATTERS = {"query_repair": _fmt_repair, "query_warranty": _fmt_warranty,
              "query_return_status": _fmt_return}

_client: MultiServerMCPClient | None = None


def _connections() -> dict:
    return {
        "repair": {"transport": "streamable_http", "url": settings.mcp_repair_url},
        "business": {"transport": "streamable_http", "url": settings.mcp_business_url},
    }


def get_client() -> MultiServerMCPClient:
    global _client
    if _client is None:
        # handle_tool_errors=False:工具错误抛 ToolException,由执行引擎统一分诊/回灌
        _client = MultiServerMCPClient(_connections(), handle_tool_errors=False)
    return _client


async def _get_tools_of(*, server_name: str):
    """薄壳:单测 monkeypatch 锚点。"""
    return await get_client().get_tools(server_name=server_name)


async def fetch_mcp_specs() -> list[ToolSpec]:
    specs: list[ToolSpec] = []
    for server in _connections():
        try:
            # 我们侧超时封顶:连接拒绝会快速失败,但 Server 假死(TCP 接了不回话)只受 adapters
            # 默认超时保护——现问现拿每步都拉清单,最坏延迟必须封住
            tools = await asyncio.wait_for(_get_tools_of(server_name=server),
                                           timeout=settings.mcp_tool_timeout)
        except Exception as e:  # noqa: BLE001 单台不可达/假死:告警+跳过,不拖垮本轮对话
            logger.warning("MCP Server「%s」不可达,本轮跳过其工具:%s", server, type(e).__name__)
            continue
        for t in tools:
            specs.append(registry.spec_from_langchain_tool(
                t, source="mcp", mcp_server=server, format_result=FORMATTERS.get(t.name)))
    return specs
