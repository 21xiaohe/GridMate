"""mock 数据源(纯函数,随机种子固定 → 同键稳定)。
interrupt 起工具实现迁至 app/tools/builtin/(注册即定义),本模块只留被 graph 节点
(fetch_workorder)与 builtin 工具共用的快照函数,不再定义任何 @tool。"""
import random


def workorder_snapshot(workorder_no: str) -> dict:
    """工单快照(纯函数,随机种子固定 → 同 workorder_no 稳定)。query_workorder 工具与 fetch_workorder 节点同源。"""
    rng = random.Random(f"workorder:{workorder_no}")
    return {
        "workorder_no": workorder_no,
        "status": rng.choice(["已受理", "现场勘查中", "施工中", "已完工"]),
        "amount": rng.randint(50, 2000),
        "created_at": f"2026-09-{rng.randint(1, 12):02d} 10:00",
        "business_type": rng.choice(["低压业扩报装", "过户更名", "计量装置更换", "表箱迁移"]),
        "repair_no": f"RX{rng.randint(10**11, 10**12 - 1)}",
    }


def owns_workorder(user_id: str, workorder_no: str) -> bool:
    """这一单是不是这个人的。归属判断只有这一处,工具和节点都调它,不各判各的。

    空 user_id 一律不放行:身份是注入进来的,注入没接上就是空串,那种情况下放行等于没做校验。"""
    if not user_id or not workorder_no:
        return False
    return any(o["workorder_no"] == workorder_no for o in list_user_workorders(user_id))


# 课程演示单。文档、curl 例子、各章验收脚本里到处写着 1001 和 2002,让每个账号名下都有
# 这两笔,例子拿来即跑。想看归属校验拦人,随便报一个别的号(如 9999)就会被挡下。
DEMO_WORKORDER_IDS = ("1001", "2002")


def list_user_workorders(user_id: str) -> list[dict]:
    """按 user_id 稳定列出该用户的工单(mock,不落库):两笔演示单 + 2-4 笔随机单。
    每笔用 workorder_snapshot 同源,前端选中后回填 workorder_no 即可 query_workorder。"""
    rng = random.Random(f"user_orders:{user_id}")
    ids = list(DEMO_WORKORDER_IDS)
    for _ in range(rng.randint(2, 4)):
        oid = str(rng.randint(1000, 9999))
        if oid not in ids:
            ids.append(oid)
    out = []
    for oid in ids:
        s = workorder_snapshot(oid)
        out.append({"workorder_no": oid, "business_type": s["business_type"],
                    "status": s["status"], "amount": s["amount"]})
    return out
