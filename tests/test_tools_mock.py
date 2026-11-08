from app.tools import business
from app.tools.builtin.workorders import query_workorder, query_meter

USER = "u-mock"
MINE = business.list_user_workorders(USER)[0]["workorder_no"]   # 这个用户名下真实存在的一单

# interrupt:query_repair 内置版下线,报修查询由报修 MCP Server 接管(见 tests/tools/test_mcp_servers.py)


async def test_query_order_deterministic_and_shaped():
    # interrupt:身份由执行引擎注入,查自己的单才有数据(归属校验见 tests/tools/test_order_ownership.py)
    r1 = await query_workorder.ainvoke({"workorder_no": MINE, "user_id": USER})
    r2 = await query_workorder.ainvoke({"workorder_no": MINE, "user_id": USER})
    assert r1 == r2                                  # 同种子可复现
    assert r1["workorder_no"] == MINE
    assert r1["status"] in {"已受理", "现场勘查中", "施工中", "已完工"}
    assert r1["repair_no"].startswith("RX")        # 报修单号,查报修需先拿它(agent 造真实依赖)


async def test_query_product_names_and_determinism():
    assert query_workorder.name == "query_workorder"
    assert query_meter.name == "query_meter"
    p1 = await query_meter.ainvoke({"model_name": "电能表"})
    p2 = await query_meter.ainvoke({"model_name": "电能表"})
    assert p1 == p2                                   # 同种子可复现
    assert p1["model_name"] == "电能表" and "price" in p1
