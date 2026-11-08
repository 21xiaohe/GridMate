# GridMate · 电力营销服务用电业务智能客服 Agent 系统

> 确定性 Workflow + Agent 混合架构的电力营销服务用电业务客服：RAG 混合检索问答、九类意图分流、工具调用查真数据、证据不足即拒答转人工，配套检索评估体系与数据飞轮。

## 解决什么问题

电力营销服务用电业务客服的三类高频诉求：**查得到**（工单/报修/业务要查真实业务库，模型不能编）、**答得准**（政策问题要依据知识库、带来源编号、可追溯）、**守得住**（答不了的不硬答，转人工/建工单）。

## 架构一览

```
用户消息
   │
   ▼
指代消解(问题补全) ──► 意图识别(九类) ──► 分流
                                        │
        ┌───────────────┬───────────────┼────────────────┐
        ▼               ▼               ▼                ▼
   RAG 检索问答      工具调用链       闲聊/兜底         建工单(人工)
   双路召回           query_workorder
   (向量+BM25)       query_repair
   RRF 融合           query_meter
   精排重排           create_ticket
        │               │
        ▼               ▼
   证据校验闸 ◄──── 工具结果回灌
   (置信度四信号)
        │
        ▼
   Agent 组织回答(带编号引用) ──► SSE 流式输出
        │
        ▼
   数据飞轮:答不上的问题入池 → 人工审核 → 写回知识库
```

- **混合检索**：BGE-M3 语义向量 + BM25 关键词双路召回，RRF 名次融合，BGE-Reranker 精排
- **幻觉治理**：回答前做证据校验（四信号加权置信度），证据不足不硬答，转人工
- **混合架构**：LangGraph 编排九类意图 → 五个出口的分流状态机；必经步骤（检索/取单/查政策）由 Workflow 调度，Agent 只负责证据齐备后的判断与应答
- **上下文管理**：分层裁剪 + 异步分段摘要 + 工具结果裁剪
- **数据飞轮**：三入口沉淀答不上的问题，人工审核后回流知识库；旁路 RoBERTa 主题分类微调给知识缺口画分布

## 技术栈

Python 3.13 · FastAPI · LangChain / LangGraph · Milvus(混合检索) · MySQL · MCP · Langfuse · SSE · Docker · RoBERTa/ONNX(主题分类)

## 快速启动

```bash
# 1. 基础设施(MySQL 3307 + Milvus Standalone 内嵌 etcd 模式)
docker compose up -d

# 2. 配置上游:复制 .env.example 为 .env,填入 CHAT/EMBED/RERANK 三组 key
cp .env.example .env

# 3. 建库 + 灌知识库
make kb-build kb-vectorize

# 4. 起服务(含两台 MCP Server)
make dev

# 5. 浏览器打开 http://localhost:8000,问「工单 1001 的报修到哪了」
#    验收:回答上方出现 query_workorder + query_repair 两个工具调用标记
```

## 评估体系

检索质量不靠感觉，靠评估集（`tests/data/eval_hybrid_strategies.jsonl`，300 题五桶：政策/型号/口语/跨文档/不可答，由 `scripts/build_evalset.py` 从知识库小节生成）：

```bash
# 四策略(向量/BM25/混合/混合+精排) × 分桶 Recall@5 / MRR 对比
uv run python -m scripts.eval_hybrid_strategies

# 评估集自检:ground truth 与知识库的机械一致性校验(防"永远答不对"的标注错误)
uv run python -m scripts.validate_evalset

# 端到端验收:Agent 全链路 / 工单流 / 摘要 / 中断+MCP
uv run python -m scripts.eval_agent_e2e
uv run python -m scripts.eval_ticket_flow
```

评估脚本与指标口径见 `EXPERIMENTS.md`；部署与开发过程中的踩坑记录（现象/根因/解法）见 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)。

## 目录结构

```
app/
  api/        # FastAPI 路由(agent/chat/kb/rageval/observability...)
  core/       # 核心组件:llm 模型工厂 / prompts / confidence 置信度 /
              # intent 意图 / rerank 精排 / memory 上下文 / flywheel 数据飞轮
  graph/      # LangGraph 工作流编排
  tools/      # 业务工具(query_workorder / query_repair / query_faq / create_ticket)
  db/         # SQLAlchemy 模型
scripts/      # 评估 / 评测 / 数据流水线 / classifier 主题分类微调
sql/          # 分模块 DDL 与种子数据
mcp_servers/  # MCP 工具服务(抢修进度等外部能力)
tests/        # pytest
```

## 设计取舍（为什么这么做）

- **Workflow 为主、Agent 为辅**：客服场景容错率低，把"必经步骤"交给确定性编排，Agent 只在证据齐备后说话——从架构上排除模型跳步骤导致的错误承诺
- **拒答优先**：置信度不过闸就转人工。答错一句的代价远高于多转一次人工
- **Milvus 内嵌 etcd 模式**：开发环境不引入额外 etcd/minio 组件，`docker compose up -d` 一条命令起全套
- **评估先行**：每个模块配套可复跑的评估脚本，调参前后有数据对比
