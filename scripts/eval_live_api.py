# 阅读导航：真实HTTP组合验收
# 本文件作用：向启动的应用发送问题，核对统计数字、案例范围、源记录和同请求版本一致性。
# 建议读法：HTTP、模型和数据服务是真实调用；运行前需启动服务，业务响应只保存在本地。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

"""真实 HTTP + 模型 + SQLite/Milvus 验收，小规模功能集，不替代质量评测。

逐题保存原始响应在忽略目录。数字参考从运行前固定的原始用户行独立计算，
不用模型解析出的条件生成参考。会实际调用配置的模型服务。
"""
# 学习注释：延迟解析类型注解，允许类型引用稍后定义的类；这行不是普通业务模块导入。
from __future__ import annotations

# 学习注释：导入argparse：解析命令行选项。
import argparse
# 学习注释：导入asyncio：异步任务、等待、锁和超时。
import asyncio
# 学习注释：导入json：Python对象与JSON文本互转。
import json
# 学习注释：导入math：数值合法性等数学操作。
import math
# 学习注释：导入re：正则表达式，匹配日期、版本或名称格式。
import re
# 学习注释：导入sys：解释器参数、导入路径与进程退出码。
import sys
# 学习注释：导入time：耗时测量。
import time
# 学习注释：从collections引入defaultdict，用于计数器和按键分组容器。
from collections import defaultdict
# 学习注释：从datetime引入datetime，用于日期时间解析与计算。
from datetime import datetime
# 学习注释：从pathlib引入Path，用于跨平台文件路径对象。
from pathlib import Path

# 学习注释：导入httpx：发送HTTP请求。
import httpx

# 学习注释：调用sys.path.insert，处理0。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# 学习注释：从app.core导入versioning，复用对应模块的实现。
from app.core import versioning
# 学习注释：共享本机配置对象settings；模型名、服务地址和凭据统一由config.py加载。
from app.config import settings

# 学习注释：10项列表：包含id、query、kind、line、start、end的字典、包含id、query、kind、center、start、end的字典、包含id、query、kind的字典、包含id、query…；保存到CASES。
CASES = [
    {"id": "line_count", "query": "线路1在2025年全年停了几次电？", "kind": "count",
     "line": "线路1", "start": "2025-01-01", "end": "2026-01-01"},
    {"id": "energy", "query": "供服中心3在2025年全年的缺供电量总和是多少？请保留两位小数。", "kind": "energy",
     "center": "供服中心3", "start": "2025-01-01", "end": "2026-01-01"},
    {"id": "ratio", "query": "台账中各类停电性质各占多少？请说明占比分母以及多标签口径。", "kind": "ratio"},
    {"id": "similar", "query": "找3条电缆故障导致停电的历史相似案例，并给出事件序号和依据。", "kind": "similar"},
    {"id": "combined", "query": "2025年6月一共发生了多少次停电？再从同一个月份内找3条电缆故障的相似案例。", "kind": "combined",
     "start": "2025-06-01", "end": "2025-07-01"},
    {"id": "outside", "query": "2024年1月一共发生了多少次停电？", "kind": "outside"},
    {"id": "partial", "query": "从2025年12月15日到2026年1月15日（不包含结束日）共有多少次停电？", "kind": "partial",
     "start": "2025-12-15", "end": "2026-01-01", "requested_end": "2026-01-15"},
    {"id": "unknown_line", "query": "线路99999发生过多少次停电？", "kind": "unknown"},
    {"id": "chat_first", "query": "线路32在2025年全年停了几次电？", "kind": "count", "chat": True,
     "line": "线路32", "start": "2025-01-01", "end": "2026-01-01"},
    {"id": "chat_followup", "query": "那这条线路在同一年6月呢？", "kind": "count", "chat": True,
     "line": "线路32", "start": "2025-06-01", "end": "2025-07-01"},
]


# 学习注释：函数reference_rows接收本次请求固定的数据版本（snapshot）；把这一段可复用处理封装起来。
def reference_rows(snapshot):
    # 学习注释：调用snapshot.connect；保存到数据库连接（conn）。
    conn = snapshot.connect()
    # 学习注释：执行可能失败的步骤；finally无论成功失败都负责清理。
    try:
        # 学习注释：返回遍历conn.execute('SELECT id,事件序号,线路名称,供服中心,停电起始时间,停电性质,缺供电量 FROM users')，将dict(row)收集为列表，结束本次函数调用。
        return [dict(row) for row in conn.execute(
            "SELECT id,事件序号,线路名称,供服中心,停电起始时间,停电性质,缺供电量 FROM users")]
    finally:
        # 学习注释：关闭conn占用的连接或资源。
        conn.close()


# 学习注释：函数selected_rows接收查询或表格行（rows）、case；把这一段可复用处理封装起来。
def selected_rows(rows, case):
    # 学习注释：返回遍历查询或表格行（rows），只保留满足条件的项，将r收集为列表，结束本次函数调用。
    return [r for r in rows
            if (not case.get("line") or r["线路名称"] == case["line"])
            and (not case.get("center") or r["供服中心"] == case["center"])
            and (not case.get("start") or case["start"] <= r["停电起始时间"] < case["end"])]


# 学习注释：函数validate接收case、response、查询或表格行（rows）、本次请求固定的数据版本（snapshot）、sources；把这一段可复用处理封装起来。
def validate(case, response, rows, snapshot, sources):
    # 学习注释：空字典；保存到checks。
    checks = {}
    # 学习注释：response.get('dataset_version')等于snapshot的版本号属性（version）；保存到checks['version']。
    checks["version"] = response.get("dataset_version") == snapshot.version
    # 学习注释：response.get('data_consistency')等于'verified_bundle'；保存到checks['verified_bundle']。
    checks["verified_bundle"] = response.get("data_consistency") == "verified_bundle"
    # 学习注释：response.get('verification_status')等于'faithful'；保存到checks['faithfulness']。
    checks["faithfulness"] = response.get("verification_status") == "faithful"
    # 学习注释：response.get('degraded')是同一对象/空值False（不成立）；保存到checks['not_degraded']。
    checks["not_degraded"] = response.get("degraded") is False
    # 学习注释：从response读取键'evidence'，缺少时取空列表；保存到bundles。
    bundles = response.get("evidence", [])
    # 学习注释：把bundles转换为真假值；保存到checks['structured_evidence']。
    checks["structured_evidence"] = bool(bundles)
    # 学习注释：按and短路取第一个假值；都为真则取最后一个值（bool(response.get('request_id')) and all((b.get('request_id') == response['request_id'] a…）；保存到checks['same_request']。
    checks["same_request"] = bool(response.get("request_id")) and all(
        b.get("request_id") == response["request_id"] and b.get("dataset_version") == snapshot.version
        for b in bundles)
    # 学习注释：遍历bundles，只保留满足条件的项，将b['stat']收集为列表；保存到stats。
    stats = [b["stat"] for b in bundles if b.get("stat")]
    # 学习注释：遍历bundles，将c收集为列表；保存到案例列表（cases）。
    cases = [c for b in bundles for c in b.get("cases", [])]
    # 学习注释：case中键/位置为'kind'的值；保存到kind。
    kind = case["kind"]
    # 学习注释：调用selected_rows，处理查询或表格行（rows）；保存到expected_rows。
    expected_rows = selected_rows(rows, case)
    # 学习注释：遍历expected_rows，将str(r['事件序号'])收集为集合；保存到满足范围条件的事件ID（eligible）。
    eligible = {str(r["事件序号"]) for r in expected_rows}
    # 学习注释：只有kind包含在4项元组：'count'、'combined'、'partial'、'energy'才进入这一分支。
    if kind in ("count", "combined", "partial", "energy"):
        # 学习注释：把stats转换为真假值；保存到checks['stat_called']。
        checks["stat_called"] = bool(stats)
        # 学习注释：kind等于'energy'时取调用round，处理累计各项数值（布尔值True按1计数），否则取计算满足范围条件的事件ID（eligible）的元素数量；保存到期望状态（expected）。
        expected = (round(sum(float(r["缺供电量"] or 0) for r in expected_rows), 2)
                    if kind == "energy" else len(eligible))
        # 学习注释：False（不成立）；保存到matched。
        matched = False
        # 学习注释：遍历stats，每轮把当前项交给统计证据或文件属性（stat）。
        for stat in stats:
            # 学习注释：stat中键/位置为'effective_filters'的值；保存到fields。
            fields = stat["effective_filters"]
            # 学习注释：从fields读取键'time_range'，缺少时取空字典；保存到tr。
            tr = fields.get("time_range", {})
            # 学习注释：按and短路取第一个假值；都为真则取最后一个值（fields.get('line_name') == case.get('line') and fields.get('supply_center') == case.get('…）；保存到param_ok。
            param_ok = (fields.get("line_name") == case.get("line")
                        and fields.get("supply_center") == case.get("center")
                        and tr.get("start_time") == case["start"]
                        and tr.get("end_time") == case["end"])
            # 学习注释：kind等于'energy'时取'缺供电量总和'，否则取'停电次数'；保存到key。
            key = "缺供电量总和" if kind == "energy" else "停电次数"
            # 学习注释：stat['rows']为真/非空时取从stat['rows'][0]读取键key，缺少时取None，否则取None（尚无值）；保存到value。
            value = stat["rows"][0].get(key) if stat["rows"] else None
            # 学习注释：只有param_ok为真/非空，并且检查value是否属于(int, float)类型，并且调用math.isclose，处理value才进入这一分支。
            if param_ok and isinstance(value, (int, float)) and math.isclose(value, expected, abs_tol=.01):
                # 学习注释：True（成立）；保存到matched。
                matched = True
        # 学习注释：matched；保存到checks['stat_parameters_and_value']。
        checks["stat_parameters_and_value"] = matched
        # 数字出现检查仅用于定位；最终事实还需要下方真实响应人工复核。
        answer = str(response.get("answer", "")).replace(",", "").replace("，", "")
        # 学习注释：检查是否至少有一项条件成立；保存到checks['answer_contains_reference_number']。
        checks["answer_contains_reference_number"] = any(
            math.isclose(float(n), expected, abs_tol=.01)
            for n in re.findall(r"\d+(?:\.\d+)?", answer))
        # 学习注释：只有kind等于'energy'才进入这一分支。
        if kind == "energy":
            # 学习注释：不满足（把调用re.search，处理'(?:并按|先按|按)事件去重(?:后|计数后).{0,24}(?:求和|累加)'转换为真假值）；保存到checks['no_event_dedup_sum_claim']。
            checks["no_event_dedup_sum_claim"] = not bool(
                re.search(r"(?:并按|先按|按)事件去重(?:后|计数后).{0,24}(?:求和|累加)", answer))
        # 学习注释：只有kind等于'partial'才进入这一分支。
        if kind == "partial":
            # 学习注释：检查是否至少有一项条件成立；保存到checks['partial_coverage']。
            checks["partial_coverage"] = any(
                stat.get("coverage_status") == "partial"
                and stat["requested_filters"].get("time_range", {}).get("end_time") == case["requested_end"]
                for stat in stats)
    # 学习注释：只有kind等于'ratio'才进入这一分支。
    if kind == "ratio":
        # 学习注释：计算遍历查询或表格行（rows），将r['事件序号']收集为集合的元素数量；保存到denominator。
        denominator = len({r["事件序号"] for r in rows})
        # 学习注释：调用defaultdict，处理set；保存到labels。
        labels = defaultdict(set)
        # 学习注释：遍历查询或表格行（rows），每轮把当前项交给r。
        for r in rows:
            # 学习注释：只有r['停电性质']为真/非空才进入这一分支。
            if r["停电性质"]:
                # 学习注释：调用labels[r['停电性质']].add，处理r中键/位置为'事件序号'的值。
                labels[r["停电性质"]].add(r["事件序号"])
        # 学习注释：False（不成立）；保存到matched。
        matched = False
        # 学习注释：遍历stats，每轮把当前项交给统计证据或文件属性（stat）。
        for stat in stats:
            # 学习注释：遍历stat['rows']，将r['停电性质']→r收集为字典；保存到actual。
            actual = {r["停电性质"]: r for r in stat["rows"]}
            # 学习注释：用按and短路取第一个假值；都为真则取最后一个值（stat.get('total_events') == denominator and set(actual) == set(labels) and all((actual[k]…）更新matched；下方+=等写法把计算结果存回原变量。
            matched |= stat.get("total_events") == denominator and set(actual) == set(labels) and all(
                actual[k]["停电次数"] == len(ids)
                and math.isclose(actual[k]["占比_pct"], round(len(ids) * 100 / denominator, 2), abs_tol=.01)
                for k, ids in labels.items())
        # 学习注释：matched；保存到checks['ratio_values_and_denominator']。
        checks["ratio_values_and_denominator"] = matched
        # 学习注释：不满足（把调用re.search，处理'(?:可以|可再|能再).{0,35}(?:单标签|按线路|按供服中心)'转换为真假值）；保存到checks['no_unsupported_offer']。
        checks["no_unsupported_offer"] = not bool(re.search(
            r"(?:可以|可再|能再).{0,35}(?:单标签|按线路|按供服中心)", str(response.get("answer", ""))))
    # 学习注释：只有kind包含在2项元组：'similar'、'combined'才进入这一分支。
    if kind in ("similar", "combined"):
        # 学习注释：把案例列表（cases）转换为真假值；保存到checks['cases_returned']。
        checks["cases_returned"] = bool(cases)
        # 学习注释：检查是否每一项条件都成立；保存到checks['case_scope']。
        checks["case_scope"] = all(c["event_id"] in eligible for c in cases)
        # 学习注释：检查是否每一项条件都成立；保存到checks['source_text_and_rows']。
        checks["source_text_and_rows"] = all(
            c["event_id"] in sources and c["text"] == sources[c["event_id"]]["事件文本"]
            and set(c["source_row_ids"]) == {r["id"] for r in rows if str(r["事件序号"]) == c["event_id"]}
            for c in cases)
        # 学习注释：只有kind等于'combined'才进入这一分支。
        if kind == "combined":
            # 学习注释：检查是否每一项条件都成立；保存到checks['case_time_filter']。
            checks["case_time_filter"] = all(
                c["effective_filters"].get("time_range") == {"start": case["start"], "end": case["end"]}
                and c.get("time_semantics") == "record_start" for c in cases)
    # 学习注释：只有kind等于'outside'才进入这一分支。
    if kind == "outside":
        # 学习注释：按and短路取第一个假值；都为真则取最后一个值（any((b['status'] == 'out_of_coverage' for b in bundles)) and (not stats)）；保存到checks['out_of_coverage']。
        checks["out_of_coverage"] = any(b["status"] == "out_of_coverage" for b in bundles) and not stats
    # 学习注释：只有kind等于'unknown'才进入这一分支。
    if kind == "unknown":
        # 学习注释：按and短路取第一个假值；都为真则取最后一个值（any((b['status'] in ('low_confidence', 'invalid_filters') for b in bundles)) and (not sta…）；保存到checks['unknown_rejected']。
        checks["unknown_rejected"] = any(b["status"] in ("low_confidence", "invalid_filters") for b in bundles) and not stats
    # 学习注释：返回checks，结束本次函数调用。
    return checks


# 学习注释：脚本主流程：按顺序准备环境、执行各步骤并保存结果；底部命令行入口会调用它。
async def main(base_url, output_dir, selected=None):
    # 学习注释：versioning.load_snapshot的执行结果；保存到本次请求固定的数据版本（snapshot）。
    snapshot = versioning.load_snapshot()
    # 学习注释：只有snapshot.consistency不等于'verified_bundle'才进入这一分支。
    if snapshot.consistency != "verified_bundle":
        # 学习注释：以异常终止这一条路径：'真实验收要求先完成业务数据发布'；上层不能把它当作正常成功。
        raise RuntimeError("真实验收要求先完成业务数据发布")
    # 学习注释：调用reference_rows，处理本次请求固定的数据版本（snapshot）；保存到查询或表格行（rows）。
    rows = reference_rows(snapshot)
    # 学习注释：遍历(json.loads(line) for line in snapshot.corpus_path.read_text(encoding='utf-8').splitlines())，将str(e['事件序号'])→e收集为字典；保存到sources。
    sources = {str(e["事件序号"]): e for e in
               (json.loads(line) for line in snapshot.corpus_path.read_text(encoding="utf-8").splitlines())}
    # 学习注释：计算 output_dir / versioning.new_version()；保存到本轮报告目录（folder）。
    folder = output_dir / versioning.new_version()
    # 学习注释：创建目录；parents控制父目录创建，exist_ok控制已存在时是否报错。
    folder.mkdir(parents=True, exist_ok=False)
    # 学习注释：计算 'live_acceptance_' + versioning.new_version()；保存到session。
    session = "live_acceptance_" + versioning.new_version()
    # 学习注释：空列表；保存到结果列表（results）。
    results = []
    # 学习注释：遍历CASES，只保留满足条件的项，将case收集为列表；保存到chosen。
    chosen = [case for case in CASES if selected is None or case["id"] in selected]
    # 学习注释：只有检查是否至少有一项条件成立，并且不满足（检查是否至少有一项条件成立）才进入这一分支。
    if any(c["id"] == "chat_followup" for c in chosen) and not any(c["id"] == "chat_first" for c in chosen):
        # 学习注释：以异常终止这一条路径：'追问复测必须包含 chat_first，避免没有历史'；上层不能把它当作正常成功。
        raise ValueError("追问复测必须包含 chat_first，避免没有历史")
    # 学习注释：包含dataset_version、chat_model、embedding_model、reranker_model、started_at、base_url的字典；保存到meta。
    meta = {"dataset_version": snapshot.version, "chat_model": settings.chat_model,
            "embedding_model": settings.embed_model, "reranker_model": settings.rerank_model,
            "started_at": datetime.now().isoformat(timespec="seconds"), "base_url": base_url}
    # 学习注释：创建HTTP客户端，用于发送请求并管理连接；with结束时调用退出逻辑，关闭资源或恢复上下文。
    async with httpx.AsyncClient(base_url=base_url, timeout=240) as client:
        # 学习注释：向指定地址发送HTTP GET请求，await等待结果后再继续；保存到health。
        health = await client.get("/healthz")
        # 学习注释：遇到非成功HTTP状态时抛异常，不把错误响应当正常数据。
        health.raise_for_status()
        # 学习注释：向指定地址发送HTTP GET请求，await等待结果后再继续；保存到home。
        home = await client.get("/")
        # 学习注释：按and短路取第一个假值；都为真则取最后一个值（health.json().get('status') == 'ok' and home.status_code == 200）；保存到meta['health_and_home']。
        meta["health_and_home"] = health.json().get("status") == "ok" and home.status_code == 200
        # 学习注释：执行可能失败的步骤；finally无论成功失败都负责清理。
        try:
            # 学习注释：遍历chosen，每轮把当前项交给case。
            for case in chosen:
                # 学习注释：读取单调高精度时钟，用于相减计算耗时；保存到本阶段计时起点（t0）。
                t0 = time.perf_counter()
                # 学习注释：包含id、query、status的字典；保存到record。
                record = {"id": case["id"], "query": case["query"], "status": "fail"}
                # 学习注释：执行可能失败的步骤；except处理异常，异常处理后由上层决定返回、重试或继续。
                try:
                    # 学习注释：从case读取键'chat'，缺少时取None时取'/api/chat'，否则取'/api/ask'；保存到endpoint。
                    endpoint = "/api/chat" if case.get("chat") else "/api/ask"
                    # 学习注释：包含query的字典；保存到发送给服务的请求体（payload）。
                    payload = {"query": case["query"]}
                    # 学习注释：将新字段合并到发送给服务的请求体（payload），同名字段会覆盖旧值。
                    payload.update({"session_id": session} if case.get("chat") else {"self_check": True})
                    # 学习注释：向指定地址发送HTTP POST请求，await等待结果后再继续；保存到response。
                    response = await client.post(endpoint, json=payload)
                    # 学习注释：response的status_code属性（status_code）；保存到record['http_status']。
                    record["http_status"] = response.status_code
                    # 学习注释：把HTTP响应正文解析为JSON对象；保存到raw。
                    raw = response.json()
                    # 学习注释：versioning.atomic_json的执行结果。
                    versioning.atomic_json(folder / (case["id"] + ".json"), raw)
                    # 学习注释：只有response.status_code等于200才进入这一分支。
                    if response.status_code == 200:
                        # 学习注释：调用validate，处理case；保存到record['checks']。
                        record["checks"] = validate(case, raw, rows, snapshot, sources)
                        # 学习注释：检查是否每一项条件都成立时取'pass'，否则取'fail'；保存到record['status']。
                        record["status"] = "pass" if all(record["checks"].values()) else "fail"
                        # 学习注释：从raw读取键'tool_calls'，缺少时取空列表；保存到record['tools']。
                        record["tools"] = raw.get("tool_calls", [])
                        # 学习注释：从raw读取键'verification_status'，缺少时取None；保存到record['verification_status']。
                        record["verification_status"] = raw.get("verification_status")
                    else:
                        # 学习注释：'http_error'；保存到record['error_type']。
                        record["error_type"] = "http_error"
                except Exception as exc:
                    # 学习注释：type(exc)的__name__属性（__name__）；保存到record['error_type']。
                    record["error_type"] = type(exc).__name__
                # 学习注释：调用round，处理计算 time.perf_counter() - t0；保存到record['elapsed_seconds']。
                record["elapsed_seconds"] = round(time.perf_counter() - t0, 2)
                # 学习注释：向结果列表（results）末尾添加record。
                results.append(record)
                # 学习注释：versioning.atomic_json的执行结果。
                versioning.atomic_json(folder / "result.json", {"meta": meta, "results": results})
                # 学习注释：在命令行输出本步骤结果或进度。
                print(json.dumps(record, ensure_ascii=False), flush=True)
                # 账号/认证异常时停止后续调用，保留已经完成的结果。
                if record.get("http_status") in (401, 402, 403):
                    # 学习注释：立即退出当前循环；不会继续处理本循环后面的候选或重试。
                    break
        finally:
            # 学习注释：向指定地址发送HTTP DELETE请求，await等待结果后再继续；保存到response。
            response = await client.delete("/api/chat/" + session)
            # 学习注释：response.status_code等于200；保存到meta['session_cleared']。
            meta["session_cleared"] = response.status_code == 200
    # 学习注释：累计各项数值（布尔值True按1计数）；保存到passed。
    passed = sum(r["status"] == "pass" for r in results)
    # 学习注释：将新字段合并到meta，同名字段会覆盖旧值。
    meta.update(completed=len(results), total=len(chosen), passed=passed,
                status="pass" if passed == len(chosen) and meta["session_cleared"] else "fail")
    # 学习注释：versioning.atomic_json的执行结果。
    versioning.atomic_json(folder / "result.json", {"meta": meta, "results": results})
    # 学习注释：在命令行输出本步骤结果或进度。
    print(json.dumps({"folder": str(folder), "summary": meta}, ensure_ascii=False), flush=True)
    # 学习注释：返回meta['status']等于'pass'时取0，否则取1，结束本次函数调用。
    return 0 if meta["status"] == "pass" else 1


# 学习注释：仅在直接运行此脚本时执行入口；被其他模块import时不自动执行下面的演示/命令。
if __name__ == "__main__":
    # 学习注释：调用argparse.ArgumentParser；保存到parser。
    parser = argparse.ArgumentParser(description=__doc__)
    # 学习注释：调用parser.add_argument，处理'--base-url'。
    parser.add_argument("--base-url", default="http://127.0.0.1:8116")
    # 学习注释：调用parser.add_argument，处理'--output-dir'。
    parser.add_argument("--output-dir", type=Path, default=Path("data/evaluations/live_api"))
    # 学习注释：调用parser.add_argument，处理'--cases'。
    parser.add_argument("--cases", nargs="+", choices=[c["id"] for c in CASES])
    # 学习注释：调用parser.parse_args；保存到args。
    args = parser.parse_args()
    # 学习注释：调用sys.exit，处理启动事件循环，执行脚本的异步主函数。
    sys.exit(asyncio.run(main(args.base_url, args.output_dir, args.cases)))
