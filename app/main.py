# -*- coding: utf-8 -*-
# 阅读导航：HTTP入口
# 本文件作用：网页把问题发到 /api/chat；此处校验请求结构并交给 agent.chat，最终把字典转成JSON响应。
# 建议读法：先读 AskRequest / ChatRequest，再读 api_ask / api_chat；算法在 app/core 中。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

"""GridMate（停电台账问数助手）应用入口。

对外提供「停电台账问数」能力的轻量 HTTP 接口 + 网页聊天框（面试演示用）。

两个接口：
  POST /api/ask   —— 单轮问数（不保留多轮历史）
  POST /api/chat  —— 多轮会话（带指代消解，session_id 标识会话）

返回值统一包含：
  answer        最终回答文本
  tool_calls    本次实际调用的工具名列表（验证「Agent 自主编排」）
  resolved_query 多轮时才有的「指代消解补全后的问题」
  verification_status 忠实度三态 "faithful"/"unfaithful"/"unverified"/null（null=未自检）
  faith_reason  忠实度判定依据
  degraded      是否降级为直接呈现原始数据
"""
# 学习注释：导入logging：分级日志，便于排查运行失败。
import logging
# 学习注释：从contextlib引入asynccontextmanager，用于上下文管理器，管理进入/退出步骤。
from contextlib import asynccontextmanager
# 学习注释：从pathlib引入Path，用于跨平台文件路径对象。
from pathlib import Path

# 学习注释：从fastapi引入FastAPI, HTTPException，用于定义HTTP应用、接口与请求错误。
from fastapi import FastAPI, HTTPException
# 学习注释：从fastapi.responses引入FileResponse，用于返回网页或文件响应。
from fastapi.responses import FileResponse
# 学习注释：从fastapi.staticfiles引入StaticFiles，用于提供CSS、JS和HTML等静态资源。
from fastapi.staticfiles import StaticFiles
# 学习注释：从pydantic引入BaseModel, Field，用于字段类型、默认值与数据结构校验。
from pydantic import BaseModel, Field

# 学习注释：从app.core导入agent，复用对应模块的实现。
from app.core import agent

# 学习注释：创建以当前模块名标识的日志记录器；保存到logger。
logger = logging.getLogger(__name__)
# 学习注释：按目录层级拼接文件路径 Path(__file__).parent / 'static'；保存到_STATIC_DIR。
_STATIC_DIR = Path(__file__).parent / "static"


# 学习注释：异步函数lifespan接收app；把这一段可复用处理封装起来。
@asynccontextmanager
# 学习注释：FastAPI的启动/关闭上下文；当前yield前后没有额外动作，外部服务不在这里启动。
async def lifespan(app: FastAPI):
    # 学习注释：把控制权交给with块或迭代调用者；恢复后继续执行清理步骤。
    yield


# 学习注释：创建HTTP应用对象；Uvicorn启动时加载的app.main:app就是这个变量。
app = FastAPI(title="GridMate", version="0.2.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# 请求 / 响应模型
# ---------------------------------------------------------------------------
# 学习注释：AskRequest定义数据字段与默认值；继承的Pydantic会在创建/解析时检查类型和约束。
class AskRequest(BaseModel):
    # 学习注释：请求正文中的query是用户输入；Pydantic检查字符串类型，此处没有单独限制长度。
    query: str = Field(description="用户的自然语言问数问题")
    # 学习注释：单轮接口默认开启回答自检；显式传false时会跳过这项模型检查。
    self_check: bool = Field(default=True, description="是否生成后跑忠实度自检")


# 学习注释：ChatRequest定义数据字段与默认值；继承的Pydantic会在创建/解析时检查类型和约束。
class ChatRequest(BaseModel):
    # 学习注释：用同一会话ID关联多轮问题；它只是会话键，目前没有登录认证或用户归属校验。
    session_id: str = Field(description="会话标识，同一会话多轮用同一个 id")
    # 学习注释：请求正文中的query是用户输入；Pydantic检查字符串类型，此处没有单独限制长度。
    query: str = Field(description="用户的自然语言问数问题")


# ---------------------------------------------------------------------------
# 接口
# ---------------------------------------------------------------------------
# 学习注释：注册单轮POST接口；请求JSON按AskRequest解析后传给下面的函数。
@app.post("/api/ask")
async def api_ask(req: AskRequest) -> dict:
    """单轮问数：输入一句自然语言，返回 Agent 自主编排后的回答。"""
    # 学习注释：执行可能失败的步骤；except处理异常，异常处理后由上层决定返回、重试或继续。
    try:
        # 学习注释：等待单轮Agent完成，拿到回答、实际工具调用和结构化证据。
        result = await agent.run(req.query, self_check=req.self_check)
    except Exception:
        # 学习注释：在服务端保留异常栈便于排查；本地日志可能含请求信息，不应直接公开。
        logger.exception("问数失败 query=%r", req.query)
        # 学习注释：向浏览器返回固定错误说明与500状态，避免把上游异常正文直接展示。
        raise HTTPException(status_code=500, detail="问数执行失败，请稍后重试。") from None
    # 学习注释：返回result，结束本次函数调用。
    return result


# 学习注释：注册多轮POST接口；chat会读取历史并补全追问。
@app.post("/api/chat")
async def api_chat(req: ChatRequest) -> dict:
    """多轮会话：同一 session_id 下的追问会自动做指代消解（「这条线」「它」）。"""
    # 学习注释：执行可能失败的步骤；except处理异常，异常处理后由上层决定返回、重试或继续。
    try:
        # 学习注释：把会话ID与原问题交给多轮处理；本接口本身不计算统计数字。
        result = await agent.chat(req.session_id, req.query)
    except Exception:
        # 学习注释：在服务端保留异常栈便于排查；本地日志可能含请求信息，不应直接公开。
        logger.exception("会话失败 session_id=%r", req.session_id)
        # 学习注释：向浏览器返回固定错误说明与500状态，避免把上游异常正文直接展示。
        raise HTTPException(status_code=500, detail="会话执行失败，请稍后重试。") from None
    # 学习注释：返回result，结束本次函数调用。
    return result


# 学习注释：注册清空会话接口；清空与请求写回使用同一把锁。
@app.delete("/api/chat/{session_id}")
async def api_clear_session(session_id: str) -> dict:
    """清空某会话的多轮历史（重置对话，会话内存有上限，也可手动释放）。"""
    # 学习注释：agent.clear_session的执行结果，await等待结果后再继续。
    await agent.clear_session(session_id)
    # 学习注释：返回包含status、cleared的字典，结束本次函数调用。
    return {"status": "ok", "cleared": session_id}


# 学习注释：注册进程存活接口；它不会探测模型额度、网络和Milvus是否可用。
@app.get("/healthz")
async def healthz() -> dict:
    # 学习注释：仅报告本HTTP应用正在响应，不能据此推断整条问答链路健康。
    return {"status": "ok", "service": "gridmate-outage-qa"}


# ---------------------------------------------------------------------------
# 网页聊天框
# ---------------------------------------------------------------------------
# 学习注释：异步函数chat_page接收调用时的上下文；把这一段可复用处理封装起来。
@app.get("/", include_in_schema=False)
async def chat_page() -> FileResponse:
    # 学习注释：把聊天页HTML返回给浏览器；网页中的JavaScript再调用/api/chat。
    return FileResponse(_STATIC_DIR / "index.html")


# 学习注释：将/static路径映射到静态文件目录，供浏览器加载页面资源。
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
