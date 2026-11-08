"""指代消解标注评估:多轮带指代补全 + 已完整透传。需聊天上游可调通。
用法:.venv/bin/python -m scripts.eval_coref"""
import asyncio

from app.core.coref import resolve

# (history, query, 期望:'rewrite' 补出实体 / 'passthrough' 原样)
CASES = [
    ("用户:智能电表什么时候来装\n客服:预计明天上门", "这个能退吗", "rewrite"),
    ("用户:工单1001买的电能表\n客服:已装表", "它到哪了", "rewrite"),
    ("", "智能电表的检定周期是多久", "passthrough"),
    ("", "退费违约金谁承担", "passthrough"),
]


async def main():
    for hist, q, kind in CASES:
        got = await resolve(q, hist)
        changed = got != q
        ok = (changed and kind == "rewrite") or (not changed and kind == "passthrough")
        print(f"{'✅' if ok else '❌'} {q!r} -> {got!r} 期望={kind}")
    print("\n(glm 非确定性;透传类必须不改,补全类应含上文实体。抖动如实重跑记录)")


if __name__ == "__main__":
    asyncio.run(main())
