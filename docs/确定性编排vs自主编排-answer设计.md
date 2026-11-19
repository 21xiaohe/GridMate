# 设计文档：确定性编排 vs 自主编排（原 answer.py）

> 本文件原为 `app/core/answer.py`，因**已无调用方**（当前对外主链路是 `agent.py` 的
> ReAct 循环），从代码目录移入 `docs/` 作为设计文档保留。以下内容仅作设计参考，
> 不作为可运行模块使用。

## 定位：联合回答组装层（结构化统计 + 文本证据 + 可追溯）

把两类能力的结果组装成一个带「证据」字段的回答结构：
  - 结构化统计（ask.py，SQL）→ 数字结论附「来源模板 + 口径 + 过滤条件」
  - 相似事件（similar.py，RAG）→ 文字结论附「事件序号」（天然可溯源）

## 与 Agent 主链路的关系（两条路线的取舍）

当前对外主链路是 agent.py 的 ReAct 循环——模型自主调 tools.py 里的两个工具，
证据由工具返回内容 + faithfulness.py 自检承载。本模块是**工具化之前**的
「确定性组装」设计：由代码显式编排「先统计、再检索、证据分列」，不经过模型决策。

两者是同一难点的两种解法：

| 路线 | 编排方式 | 优点 | 代价 |
|---|---|---|---|
| agent.py | 模型自主编排 | 灵活，能组合未知的问法 | 多一次 LLM 决策，有选错工具的风险 |
| answer.py（本设计） | 代码确定性编排 | 稳定，口径和证据结构完全可控 | 只能覆盖预定义的组合 |

**最终选型：agent.py（自主编排）**。理由——问数场景的用户问法开放，代码穷举不完
「先查数再找案例」这类组合；而口径安全靠「工具薄封装」保住了（模型选工具，
代码决定怎么做对），所以自主编排的灵活性收益大于选错工具的风险。answer.py 这条
确定性路线作为备选保留，若某天发现自主编排在特定场景下不稳定，可切回（改 main.py
或 tools.py 一行 import 即可）。

## 设计原则

  - 数字结论的依据是**确定性代码在产出时就带上的**（SQL 模板、口径），不是 LLM 事后回忆；
  - 文字结论的依据是**事件序号**，能直接回到台账定位到那条原始记录；
  - 组装结果是一个结构化 dict，供上层（LLM 生成最终话术 / 前端渲染）消费，
    回答里的每一个关键结论都能对应到 evidence 里的一项。

## 返回结构

```
{
  "answer": str,                 # 面向用户的一句话结论
  "evidence": [
    {"type": "sql",  "conclusion": "159 次", "template": ..., "granularity": ..., "filters": [...]},
    {"type": "event", "conclusion": "内部故障停电事件", "event_id": "2025...", "attrs": {...}},
    ...
  ]
}
```

---

## 参考实现（以下为原代码，仅作设计参考）

```python
from __future__ import annotations

from app.core import ask as ask_mod
from app.core import similar


async def answer_ask(query: str) -> dict:
    """问数场景：结构化统计 + SQL 溯源。"""
    r = await ask_mod.ask(query)
    if r.get("status") != "ok":
        # 低置信 / 出错：不硬答，直接透传说明（此时没有可信的数字可溯源）
        return {
            "answer": r.get("message", ""),
            "evidence": [],
            "status": r.get("status"),
        }
    ev = r["evidence"]
    return {
        "answer": r["message"],
        "status": "ok",
        "evidence": [{
            "type": "sql",
            "conclusion": r["message"],
            "template": ev["template_id"],
            "template_desc": ev["template_desc"],
            "granularity": ev["granularity"],
            "filters": ev["filters"],
            "source_table": ev["source_table"],
        }],
    }


async def answer_similar(query: str, top_k: int = 5) -> dict:
    """相似事件场景：文本证据 + 事件序号溯源。"""
    events = await similar.search_similar(query, top_k=top_k)
    if not events:
        return {"answer": "没有找到相似的历史停电事件。", "status": "ok", "evidence": []}

    evidence = []
    for e in events:
        evidence.append({
            "type": "event",
            "conclusion": f"{e.get('停电性质')}事件（{e.get('起始时间', '')[:10]}）",
            "event_id": e.get("事件序号"),
            "attrs": {
                "停电性质": e.get("停电性质"),
                "电压等级": e.get("电压等级"),
                "供服中心": e.get("供服中心"),
                "线路名称": e.get("线路名称"),
                "时户数合计": e.get("时户数合计"),
                "缺供电量合计": e.get("缺供电量合计"),
            },
            "rerank_score": e.get("_rerank_score"),
        })

    top = events[0]
    answer = (f"找到 {len(events)} 个相似事件，最相关的是事件 {top.get('事件序号')}"
              f"（{top.get('停电性质')}，{top.get('起始时间', '')[:10]}）。")
    return {"answer": answer, "status": "ok", "evidence": evidence}


async def answer_combined(stat_query: str, similar_query: str, top_k: int = 3) -> dict:
    """组合场景：先统计口径（SQL），再找相似事件佐证（RAG），两者证据分列。

    对应方案 3.2 节的「组合执行」——统计依据和文本来源分别标明，不混在一起。
    """
    stat = await answer_ask(stat_query)
    sim = await answer_similar(similar_query, top_k=top_k)

    if stat["status"] != "ok":
        return sim  # 统计没成，退回纯相似检索结果

    return {
        "answer": stat["answer"],
        "status": "ok",
        "evidence": stat["evidence"] + sim["evidence"],  # SQL 依据在前，文本依据在后
        "note": "数字结论来自 SQL 统计，相似事件来自语义检索，两类依据分别标注。",
    }


if __name__ == "__main__":
    import asyncio

    async def _demo():
        print("=== 1. 问数（SQL 溯源） ===")
        r = await answer_ask("今年6月一共停了多少次电")
        print(f"答案：{r['answer']}")
        for ev in r["evidence"]:
            print(f"  依据[{ev['type']}]: {ev['template_desc']} | 口径={ev['granularity']} | 过滤={ev['filters']}")

        print("\n=== 2. 相似事件（事件序号溯源） ===")
        r = await answer_similar("电缆故障导致的停电", top_k=3)
        print(f"答案：{r['answer']}")
        for ev in r["evidence"]:
            print(f"  依据[{ev['type']}]: 事件{ev['event_id']} | {ev['conclusion']} | 重排分{ev['rerank_score']:.3f}")

        print("\n=== 3. 组合（统计 + 相似，证据分列） ===")
        r = await answer_combined("停电原因占比", "内部故障停电", top_k=2)
        print(f"答案：{r['answer']}")
        print(f"说明：{r['note']}")
        for ev in r["evidence"]:
            if ev["type"] == "sql":
                print(f"  [SQL依据] {ev['conclusion']} ← {ev['template_desc']}（口径{ev['granularity']}）")
            else:
                print(f"  [事件依据] 事件{ev['event_id']} ← {ev['conclusion']}")

    asyncio.run(_demo())
```
