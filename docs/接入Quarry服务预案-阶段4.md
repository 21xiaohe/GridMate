# GridMate 接入独立 Quarry 服务 · 预案（阶段4）

日期：2026-11-16
状态：**预案，未实施**。不修改当前运行链路，Quarry 服务本机未运行。

---

## 一、结论先行

**当前不接。** 整合建议第 6 节的阶段4定位是「按实际需求决定是否接入」，触发前提是出现以下**明确需求之一**：

1. 需要检索**长篇故障报告 / 规程文档 / 检修手册**（当前事件备注是几十字的短文本，Milvus 事件级检索已够用）；
2. **多应用共享**同一套文档检索（当前 GridMate 是单应用，无共享诉求）；
3. 需要 Quarry 的**事实核实层**（旅游场景的"票价/开放时间"官方值替换，停电场景暂无对应易变数值）。

在触发条件出现前，强行接入只有成本没有收益：本机要拉起 Postgres + pgvector + Redis 三件套，停电备注还要重新导入、重新评测，而检索效果并不因此提升（整合建议第五节的明确判断）。

**本预案的价值**：把"未来真接时"的职责边界、接口契约、字段映射、部署依赖、验收标准一次想清楚，避免真到那天从零摸索。

---

## 二、职责边界（谁干什么，不越界）

| 模块 | 现状（已保留） | 接入 Quarry 后的变化 |
|---|---|---|
| GridMate 问数（SQL 统计） | 保留，参数化 SQL 算次数/电量/占比 | **不变**——Quarry 不碰统计数字，统计仍是确定性 SQL |
| GridMate 相似事件检索（Milvus） | 保留，事件级混合检索+重排 | **不变**——短事件备注仍走本地 Milvus |
| GridMate Agent | 保留，选工具、组织答案 | **新增一个工具**：文档证据检索 |
| Quarry 服务 | —（未运行） | 承担**长篇文档检索**，与事件检索并存 |

核心原则（沿用阶段1-3已落地的）：**统计用 SQL、事件用 Milvus、长文档才用 Quarry**。三者不互相替代，Agent 按用户问题类型路由。

---

## 三、Quarry 接口契约（本预案对接的对外形状）

来源：`quarry-rag-1 - 副本/quarry/api/` + `sdk/quarry_client.py`。以下是接入时 GridMate 要消费的部分。

### 3.1 两个端点

| 端点 | 方法 | 作用 |
|---|---|---|
| `/v1/search` | POST | 检索证据（正文 + 上下文 + 引用 + 分数 + 拒答信号） |
| `/v1/collections` | GET | 确认调用方可访问哪些集合 |

### 3.2 请求体（`SearchRequest`）

```json
{
  "collection": "规程文档",   // 集合 slug，1~64 字符
  "query": "电缆耐压试验规程",
  "top_k": 8,                 // 1~50，默认取服务端 default_top_k
  "filters": {},              // 元数据过滤（JSON 包含匹配，无日期区间语义）
  "use_rerank": true,
  "use_facts": true
}
```

### 3.3 响应体（`to_payload`）

```json
{
  "request_id": "...",
  "query": "...",
  "abstain": {
    "should_abstain": false,
    "reason": null,            // no_hits | low_score | no_signal
    "top_dense_score": 0.72,
    "threshold": 0.45
  },
  "evidences": [
    {
      "chunk_id": "...",
      "text": "命中块原文（用于高亮，不遮蔽）",
      "context": "给大模型读的上下文（开事实层时数值已遮蔽）",
      "context_level": "neighbors|section|chunk",
      "citation": {
        "source_uri": "...", "title": "...", "url": "...",
        "heading_path": ["章", "节"], "char_start": 100, "char_end": 250,
        "locator_kind": "char|paragraph|unknown"
      },
      "facts": [],             // 事实层：易变数值，旅游场景才有
      "score": 0.71, "rerank_score": 0.9, "dense_score": 0.72, "sparse_score": 0.5,
      "sibling_hits": 0
    }
  ],
  "timings": {}
}
```

### 3.4 鉴权与错误

- 鉴权：`Authorization: Bearer <api_key>`，调用方标签（权限范围）由 API Key 决定，不在请求体里传。
- 错误形状固定：`{"error": {"code": "...", "message": "...", "request_id": "..."}}`，上游按 `code` 分支。
- 客户端已实现重试：`NO_RETRY = {400,401,403,404,422}` 不重试；429 按 `Retry-After` 退避；5xx/网络错误指数退避（0.5s、1s、2s + 抖动）。

---

## 四、字段映射（Quarry 引用 → 停电台账引用）

这是接入时**最需要先定义**的一步（整合建议第五节的判断）。两者引用定位体系不同，不能直接套：

| 维度 | Quarry（文档） | GridMate 事件（已落地，阶段1） | 接入后 Quarry 文档证据 |
|---|---|---|---|
| 引用定位 | `char_start/char_end` 字符区间 | `数据集版本 + 来源表 + 事件ID` | **新增**：`数据集版本 + source_uri + chunk_id + heading_path` |
| 来源标识 | `source_uri` + `title` + `url` | `source_table`（users 表） | `source_uri`（文档路径/编号） |
| 上下文层级 | `context_level`（neighbors/section/chunk） | 事件级（备注合并） | 文档章节层级（heading_path） |
| 拒答信号 | `abstain.should_abstain`（dense 阈值 0.45） | 阶段3 的 `empty/out_of_scope` + 冲突标记 | 复用 Quarry 的 abstain，但要**重新校准阈值**（见 §六） |
| 事实核实 | `facts`（易变数值 + verified/expired） | 无（停电数字来自 SQL，非 LLM 抽取） | **不用**——停电指标仍走确定性 SQL，不引入事实层 |

**关键结论**：接入后 GridMate 会有**两套引用体系并存**——事件证据（台账行引用）+ 文档证据（Quarry 字符引用）。Agent 和忠实度检查要能区分"这个数字来自 SQL""这个案例来自 Milvus""这段规程来自 Quarry 文档"，不能混在一个证据结构里。

---

## 五、部署依赖与端口（接的时候才需要）

来源：Quarry 的 `docker-compose.yml` + `.env.example`。

| 组件 | 镜像/版本 | 端口 | 说明 |
|---|---|---|---|
| PostgreSQL + pgvector | `pgvector/pgvector:0.8.6-pg17` | 宿主机 `5433` → 容器 `5432` | pgvector 版本和 PG 大版本必须钉死 |
| Redis（含 RedisInsight） | `redis/redis-stack:7.4.0-v3` | 数据 `6380`，可视化 `8001` | 检索队列用 |
| Quarry 应用 | 本地 `uvicorn` 起 | 默认 `8100` | **与 GridMate 的 8100 冲突，须改端口** |

**端口冲突是接入时的第一个坑**（整合建议第 6 节已点名）：GridMate 网页聊天框用 8100，Quarry 服务默认也 8100。接入时 Quarry 改到比如 `8101`，GridMate 客户端指向 `http://localhost:8101`。

**Embedding 维度对齐**：Quarry 默认 `text-embedding-v4`（1024 维），GridMate 事件检索用 `bge-m3`（1024 维）。维度恰好都是 1024，但**不同 embedding 模型产生的向量不能混用**（整合建议第五节）——Quarry 文档检索用自己的 embedding，GridMate 事件检索用自己的，各自独立，绝不共享集合。

---

## 六、验收标准（真接时的检查清单）

1. **职责不串**：统计数字仍来自 SQL，不因接入 Quarry 而改走 LLM 抽取；事件案例仍来自 Milvus，只有长文档走 Quarry。
2. **引用可溯源**：文档证据能定位到 `source_uri + chunk_id + heading_path`，回答里的引用和返回证据一致。
3. **拒答阈值重新校准**：Quarry 默认 `min_dense_score=0.45` 是**旅游语料**调的，不能直接搬。必须用停电文档的相关/不相关样例重新校准，再决定启用。
4. **降级不丢可用能力**：Quarry 服务不可用时，Agent 仍能用 SQL 问数 + Milvus 事件检索回答，不把"文档服务挂了"混同"没有文档"。
5. **版本一致**：文档检索的数据版本并入现有 version.json 清单，统计/事件/文档三者同一次发布。

---

## 七、触发条件（何时启动这份预案）

出现以下任一信号时，回到本文档执行接入：

- 用户开始问"检修规程里对 XX 怎么规定的""某类故障的处理手册"这类**长文档检索**问题，且现有 Milvus 事件检索明显不够；
- 出现第二个应用需要共享同一套文档检索；
- 停电业务出现类似旅游"门票价格"的**易变数值**需要官方值替换 + 过期标记。

在以上任一出现前，**维持现状**（SQL 统计 + Milvus 事件检索 + 阶段1-3 的完整证据链路），优先把阶段1-3 的验收做实。

---

## 八、不做什么（明确划清，避免过度设计）

- 不把事件备注塞进 Quarry 重新检索（短文本事件检索已有 Milvus，重复建设）；
- 不引入 Quarry 的父子分块、邻块扩展、多租户平台（整合建议：短备注、小样本阶段不是优先项）；
- 不迁移 GridMate 的统计库到 Quarry 的 PostgreSQL（统计是确定性 SQL，无迁移收益）。
