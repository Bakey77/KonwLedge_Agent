"""
FastAPI 入口 — 企业知识管理系统 REST API

提供三组接口:
  1. /api/ingest   — 文档上传 & 入库
  2. /api/qa       — 智能问答
  3. /api/admin    — 管理（统计、更新触发）
"""

from __future__ import annotations  # 启用类型提示（Python 3.10+）

import os          # 操作系统模块（处理文件路径）
import shutil      # 文件操作模块（复制文件流）
import asyncio     # 异步事件循环模块
from contextlib import asynccontextmanager  # 异步上下文管理器
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile  # FastAPI 核心组件
from pydantic import BaseModel  # 数据验证模型

# 导入 4 个核心 Agent
from agents.doc_parser_agent import DocParserAgent
from agents.knowledge_extract_agent import KnowledgeExtractAgent
from agents.knowledge_update_agent import ChangeType, DocumentChange, KnowledgeUpdateAgent
from agents.qa_agent import QAAgent

from config import settings  # 全局配置（从 .env 加载）
from orchestrator.graph import build_knowledge_graph_workflow  # LangGraph 工作流构建器
from services.knowledge_graph import KnowledgeGraphService  # Neo4j 知识图谱服务
from services.vector_store import VectorStoreService  # 向量数据库服务

# ─────────────────────────────────────────────────────────────────
# 全局变量：服务实例（在整个应用生命周期内共享）
# ─────────────────────────────────────────────────────────────────

vector_store = VectorStoreService()      # 向量数据库实例（ChromaDB 或 PGVector）
knowledge_graph = KnowledgeGraphService()  # 知识图谱实例（Neo4j）
workflows: dict[str, Any] = {}           # 存储三条 LangGraph 工作流（ingest/qa/update）
update_agent: KnowledgeUpdateAgent | None = None  # 需要一个全局变量来保存update_agent实例，供后续调用start_watching方法使用
_main_event_loop: asyncio.AbstractEventLoop | None = None  # 保存主事件循环，供 Watchdog 线程使用


# ─────────────────────────────────────────────────────────────────
# 应用生命周期管理（启动时初始化、关闭时清理）
# ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI 生命周期管理器

    1. 启动时：
       - 创建上传目录
       - 初始化向量数据库连接
       - 初始化知识图谱连接
       - 构建三条工作流（文档入库、智能问答、增量更新）

    2. 关闭时：
       - 关闭知识图谱连接
    """
    # 创建上传目录（如果不存在）
    os.makedirs(settings.upload_dir, exist_ok=True)

    # 初始化向量数据库（ChromaDB 或 PGVector）
    try:
        await vector_store.init()
    except Exception:
        pass  # 忽略初始化失败（可能服务未启动）

    # 初始化知识图谱（Neo4j）
    try:
        await knowledge_graph.init()
    except Exception:
        pass  # 忽略初始化失败（可能服务未启动）

    # 构建三条 LangGraph 工作流，存入全局字典
    global update_agent
    workflows_dict, update_agent = build_knowledge_graph_workflow(
        vector_store=vector_store,
        knowledge_graph=knowledge_graph
    )
    workflows.update(workflows_dict)

    # 保存主事件循环引用，供 Watchdog 线程使用
    global _main_event_loop
    _main_event_loop = asyncio.get_running_loop()

    # 启动 Watchdog 文件监控（使用绝对路径，传入主事件循环）
    upload_dir_abs = os.path.abspath(settings.upload_dir)
    print(f"[DEBUG] upload_dir_abs = {upload_dir_abs}")
    print(f"[DEBUG] update_agent = {update_agent}")
    print(f"[DEBUG] Calling start_watching...")
    update_agent.start_watching(upload_dir_abs, _main_event_loop)
    print(f"[DEBUG] start_watching called successfully")

    yield  # 应用运行中...

    # 关闭时：关闭知识图谱连接
    await knowledge_graph.close()


# ─────────────────────────────────────────────────────────────────
# FastAPI 应用实例
# ─────────────────────────────────────────────────────────────────

app = FastAPI(
    title="AgentKnowledgeHub — 多Agent企业知识管理系统",  # API 文档标题
    description="支持多模态RAG、知识图谱、增量更新的企业级知识管理 API",  # API 描述
    version="1.0.0",  # API 版本
    lifespan=lifespan,  # 生命周期管理函数
)


# ─────────────────────────────────────────────────────────────────
# Request / Response 数据模型（Pydantic 自动验证）
# ─────────────────────────────────────────────────────────────────

class QuestionRequest(BaseModel):
    """问答请求：用户提出的问题"""
    question: str  # 用户问题


class QuestionResponse(BaseModel):
    """问答响应：返回答案及相关信息"""
    question: str               # 原问题
    answer: str                 # 生成的回答
    confidence: float           # 置信度（0~1）
    intent: str                 # 识别的用户意图
    sources: list[dict[str, Any]]  # 答案来源（文档片段 + 知识图谱实体）
    reasoning_steps: list[str]  # AI 推理步骤（可解释性）


class IngestResponse(BaseModel):
    """文档入库响应：返回处理结果统计"""
    file_name: str       # 文件名
    chunks_count: int    # 解析出的文档块数量
    entities_count: int  # 抽取出的实体数量
    relations_count: int  # 抽取出的关系数量
    status: str          # 处理状态（success / failed）


class StatsResponse(BaseModel):
    """系统统计响应：返回各数据库状态"""
    vector_store: dict[str, Any]      # 向量库统计（总向量数等）
    knowledge_graph: dict[str, Any]    # 知识图谱统计（总实体数、关系数等）


class UpdateRequest(BaseModel):
    """手动更新请求：触发指定文件的增量更新"""
    file_path: str       # 要更新的文件路径
    change_type: str = "modified"  # 变更类型（modified / deleted）


class UpdateResponse(BaseModel):
    """增量更新响应：返回更新结果"""
    file_path: str       # 文件路径
    vectors_added: int   # 新增向量数
    vectors_deleted: int  # 删除向量数
    entities_added: int  # 新增实体数
    relations_added: int  # 新增关系数
    success: bool        # 是否成功
    processing_time_ms: float  # 处理耗时（毫秒）


# ─────────────────────────────────────────────────────────────────
# 接口 1：文档入库（/api/ingest/*）
# ─────────────────────────────────────────────────────────────────

@app.post("/api/ingest/upload", response_model=IngestResponse, tags=["文档入库"])
async def upload_document(file: UploadFile = File(...)):
    """
    POST /api/ingest/upload

    上传并解析文档，自动入库到向量库和知识图谱

    处理流程（LangGraph 工作流）：
      1. parse       → 文档解析（PDF/图片/表格 → 文本块）
      2. extract     → 知识抽取（实体识别 + 关系抽取）
      3. store_vectors → 存入向量数据库
      4. store_graph → 存入知识图谱

    参数:
        file: 上传的文档文件（multipart/form-data）

    返回:
        IngestResponse: 包含 chunks_count, entities_count 等统计
    """
    # 第1步：保存上传文件到本地
    # file.filename = 用户上传时的文件名
    save_path = os.path.join(settings.upload_dir, file.filename or "unknown")
    with open(save_path, "wb") as f:
        # file.file 是文件流，shutil.copyfileobj 将流复制到磁盘
        shutil.copyfileobj(file.file, f)

    # 第2步：获取文档入库工作流
    ingest_wf =  workflows.get("ingest")
    if not ingest_wf:
        raise HTTPException(status_code=503, detail="Ingest workflow not initialized")

    # 第3步：执行 LangGraph 工作流（异步）
    # 输入：{"file_paths": ["/path/to/file.pdf"]}
    # 输出：{"chunks": [...], "extractions": [...], "vectors_stored": N, "entities_stored": N}
    result = await ingest_wf.ainvoke({"file_paths": [save_path]})
    # 等待ingest工作流执行完成，才能得到结果，但是这个等待过程可以去执行其他的请求，比如上传下一个文件。
    # 实际上，对于该请求来说，还是顺序执行的

    # 第4步：提取结果统计
    chunks = result.get("chunks", [])
    extractions = result.get("extractions", [])

    # 统计总实体数 = 所有 chunk 抽取的实体之和
    total_entities = sum(len(e.entities) for e in extractions)
    # 统计总关系数 = 所有 chunk 抽取的关系之和
    total_relations = sum(len(e.relations) for e in extractions)

    # 第5步：返回响应
    return IngestResponse(
        file_name=file.filename or "unknown",
        chunks_count=len(chunks),
        entities_count=total_entities,
        relations_count=total_relations,
        status="success",
    )


@app.post("/api/ingest/batch", response_model=list[IngestResponse], tags=["文档入库"])
async def upload_batch(files: list[UploadFile] = File(...)):
    """
    POST /api/ingest/batch

    批量上传文档（多次调用 upload_document）
    """
    results = []
    for file in files:
        # 逐个调用 upload_document 处理
        resp = await upload_document(file)
        results.append(resp)
    return results


# ─────────────────────────────────────────────────────────────────
# 接口 2：智能问答（/api/qa/*）
# ─────────────────────────────────────────────────────────────────

@app.post("/api/qa/ask", response_model=QuestionResponse, tags=["智能问答"])
async def ask_question(req: QuestionRequest):
    """
    POST /api/qa/ask

    智能问答 — 混合检索 + 知识图谱推理

    处理流程（LangGraph 工作流）：
      1. answer → QAAgent 处理问题
         - 向量检索（语义相似度）
         - 知识图谱检索（关系路径）
         - 混合重排序
         - LLM 生成答案

    参数:
        req: QuestionRequest，包含 question 字段

    返回:
        QuestionResponse: 答案、置信度、来源、推理步骤
    """
    # 获取问答工作流
    qa_wf = workflows.get("qa")
    if not qa_wf:
        raise HTTPException(status_code=503, detail="QA workflow not initialized")

    # 执行问答工作流
    # 输入：{"question": "刘汝蔚的职位是什么？"}
    # 输出：{"result": QAResult(...)}
    result = await qa_wf.ainvoke({"question": req.question})
    qa_result = result.get("result")

    if not qa_result:
        raise HTTPException(status_code=500, detail="QA failed")

    # 构建响应，过滤来源内容（只取前200字符避免过大）
    return QuestionResponse(
        question=qa_result.question,
        answer=qa_result.answer,
        confidence=qa_result.confidence,
        intent=qa_result.intent.value,
        sources=[
            {
                "content": c.content[:200],
                "source": c.source,
                "score": c.score,
                "type": c.retrieval_type
            }
            for c in qa_result.contexts
        ],
        reasoning_steps=qa_result.reasoning_steps,
    )


# ─────────────────────────────────────────────────────────────────
# 接口 3：系统管理（/api/admin/*）
# ─────────────────────────────────────────────────────────────────

@app.get("/api/admin/stats", response_model=StatsResponse, tags=["系统管理"])
async def get_stats():
    """
    GET /api/admin/stats

    获取系统统计信息

    返回:
        StatsResponse: 向量库和知识图谱的统计数据
    """
    # 获取向量库统计（总向量数、集合名等）
    vs_stats = await vector_store.get_stats()

    # 获取知识图谱统计（总实体数、关系数等）
    kg_stats = await knowledge_graph.get_stats()

    return StatsResponse(
        vector_store=vs_stats,
        knowledge_graph=kg_stats
    )


@app.post("/api/admin/update", response_model=UpdateResponse, tags=["系统管理"])
async def trigger_update(req: UpdateRequest):
    """
    POST /api/admin/update

    手动触发知识更新（增量更新）

    处理流程（LangGraph 工作流）：
      1. process → 知识更新 Agent 处理变更
         - 检测文档变化（新增/修改/删除）
         - 差量对比
         - 只更新变化的部分
      2. retry（如有失败）→ 重试失败的更新

    参数:
        req: UpdateRequest，包含 file_path 和 change_type

    返回:
        UpdateResponse: 更新结果统计
    """
    # 获取增量更新工作流
    update_wf = workflows.get("update")
    if not update_wf:
        raise HTTPException(status_code=503, detail="Update workflow not initialized")

    # 构造变更事件
    change = DocumentChange(
        file_path=req.file_path,
        change_type=ChangeType(req.change_type),  # modified / deleted
    )

    # 执行增量更新工作流
    result = await update_wf.ainvoke({"changes": [change]})
    results = result.get("results", [])

    if not results:
        raise HTTPException(status_code=500, detail="Update failed")

    # 取第一个结果
    r = results[0]

    return UpdateResponse(
        file_path=r.change.file_path,
        vectors_added=r.vectors_added,
        vectors_deleted=r.vectors_deleted,
        entities_added=r.entities_added,
        relations_added=r.relations_added,
        success=r.success,
        processing_time_ms=r.processing_time_ms,
    )


@app.get("/api/health", tags=["系统管理"])
async def health():
    """
    GET /api/health

    健康检查接口

    返回:
        {"status": "ok", "service": "AgentKnowledgeHub"}
    """
    return {"status": "ok", "service": "AgentKnowledgeHub"}


# ─────────────────────────────────────────────────────────────────
# 启动入口（直接运行 python -m api.main 时使用）
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn  # Uvicorn 是 FastAPI 推荐的 ASGI 服务器

    # 启动 FastAPI + Uvicorn 服务器
    # host/port 从 settings.py 读取（默认 0.0.0.0:8080）
    # reload=True 开启热更新（代码修改后自动重启）
    uvicorn.run(
        "api.main:app",  # 应用路径（模块:应用名）
        host=settings.api_host,
        port=settings.api_port,
        reload=True
    )
