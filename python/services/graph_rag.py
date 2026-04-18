"""
GraphRAG 混合检索管道 — 向量检索 + 图谱遍历 + 重排序

这是本项目的核心技术亮点之一：
  传统 RAG 只做向量检索，丢失实体间的结构化关系
  GraphRAG 将知识图谱和向量检索融合，实现多跳推理

工作流:
  Query → [向量检索分支] ────→ 合并 → 交叉重排序 → Top-K
         [图谱检索分支] ────↗

图谱检索策略:
  1. 实体链接: 从 query 中识别实体 → 在图谱中定位
  2. 子图召回: 从定位实体出发 N 跳遍历
  3. 路径推理: 找到实体间的最短路径，提供推理链
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from config import settings
from services.knowledge_graph import KnowledgeGraphService
from services.vector_store import VectorStoreService


@dataclass
class GraphRAGContext:
    content: str
    source_type: str  # "vector" | "subgraph" | "path" | "community"
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


ENTITY_LINKING_PROMPT = """\
从以下问题中提取所有实体名称。要求：
1. 提取问题中出现的**完整短语**，不要拆分
2. 实体通常是问题中连续出现的词组，如"迟到3次"、"旷工1天"、"经济补偿"
3. 不要提取单个字词，如"员工"、"迟到"、"工资"

示例：
问题："员工迟到3次会影响工资吗？"
期望实体：["员工迟到3次", "扣薪", "旷工1天"]

问题："员工考勤一直有问题，最终影响哪些方面？"
期望实体：["考勤问题", "绩效", "绩效评估", "考勤异常", "绩效评估不合格", "晋升"]

问题："员工没提前申请加班，能拿加班费吗？"
期望实体：["未申请加班", "有效加班", "加班费"]
注意："加班" 不是实体，"未申请加班" 才是完整行为

返回 JSON: {"entities": ["完整实体1", "完整实体2"]}
只返回 JSON，不要有其他内容。
"""

COMMUNITY_SUMMARY_PROMPT = """\
你是一个知识图谱分析专家。根据以下子图信息，生成一段结构化摘要。
要求：
1. 概述子图中的核心实体和关系
2. 突出实体间的关键联系
3. 指出任何有价值的推理链
"""


class GraphRAGPipeline:
    """
    GraphRAG 混合检索管道

    融合三种检索策略:
      1. 向量语义检索 — 捕获语义相似内容
      2. 图谱子图检索 — 通过实体关系进行结构化推理
      3. 社区摘要检索 — 对子图进行摘要，提供高层概览
    """

    def __init__(
        self,
        vector_store: VectorStoreService,
        knowledge_graph: KnowledgeGraphService,
    ) -> None:
        self.vector_store = vector_store
        self.knowledge_graph = knowledge_graph
        self.llm = ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            temperature=0,
            timeout=60,
        )

    async def retrieve(self, query: str, top_k: int = 10) -> list[GraphRAGContext]:
        """
        混合检索入口
        并行执行向量检索和图谱检索，然后交叉重排序
        """
        vector_results = await self._vector_search(query, top_k=top_k)
        entities = await self._entity_linking(query)
        subgraph_results = await self._subgraph_search(entities)
        path_results = await self._path_search(entities)

        all_results = vector_results + subgraph_results + path_results

        if subgraph_results:
            community_ctx = await self._community_summary(subgraph_results)
            all_results.append(community_ctx)

        reranked = self._cross_rerank(all_results, query)
        return reranked[:top_k]

    # ── Step 1: 向量检索 ─────────────────────────────────────

    async def _vector_search(self, query: str, top_k: int = 5) -> list[GraphRAGContext]:
        results = await self.vector_store.search(query, top_k=top_k)
        return [
            GraphRAGContext(
                content=doc["content"],
                source_type="vector",
                score=score,
                metadata=doc.get("metadata", {}),
            )
            for doc, score in results
        ]

    # ── Step 2: 实体链接 ─────────────────────────────────────

    async def _entity_linking(self, query: str) -> list[str]:
        messages = [
            SystemMessage(content=ENTITY_LINKING_PROMPT),
            HumanMessage(content=query),
        ]
        try:
            resp = await self.llm.ainvoke(messages)
        except Exception:
            return []
        try:
            cleaned = resp.content.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
            if "\"reasoning_content\"" in cleaned:
                cleaned = cleaned.split("\"reasoning_content\"")[0] + '"}'
            data = json.loads(cleaned)
            entities = data.get("entities", [])
            return entities if isinstance(entities, list) else []
        except (json.JSONDecodeError, IndexError, KeyError, TypeError):
            return []

    # ── Step 3: 子图检索 ─────────────────────────────────────

    async def _subgraph_search(self, entities: list[str], hops: int = 2) -> list[GraphRAGContext]:
        contexts: list[GraphRAGContext] = []
        for entity_name in entities:
            neighbors = await self.knowledge_graph.get_neighbors(entity_name, hops=hops)
            for record in neighbors:
                # relation_descs: [[desc1, desc2], [desc3], ...] → flatten → top-2 用 ； 拼接
                rel_descs_raw = record.get("relation_descs", [])
                flat_rel = [d for sub in rel_descs_raw for d in sub]  # flatten
                rel_text = "；".join(flat_rel[:2]) if flat_rel else ""
                # target_descs: [desc1, desc2, ...] 本身是字符串列表
                target_descs = record.get("target_descs", [])
                target_text = "；".join(target_descs[:2]) if target_descs else ""
                content = (
                    f"{record.get('source', '')} "
                    f"--[{rel_text}]--> "
                    f"{record.get('target', '')} "
                    f"({record.get('target_type', '')}): "
                    f"{target_text}"
                )
                contexts.append(GraphRAGContext(
                    content=content,
                    source_type="subgraph",
                    score=0.75,
                    metadata={"entity": entity_name, "hops": hops},
                ))
        return contexts

    # ── Step 4: 路径检索 ─────────────────────────────────────

    async def _path_search(self, entities: list[str]) -> list[GraphRAGContext]:
        """查找实体对之间的最短路径，提供推理链"""
        if len(entities) < 2:
            return []

        contexts: list[GraphRAGContext] = []
        for i in range(len(entities)):
            for j in range(i + 1, min(i + 3, len(entities))):
                cypher = """
                MATCH path = shortestPath(
                    (a:Entity {name: $name_a})-[*..5]-(b:Entity {name: $name_b})
                )
                RETURN
                    [n IN nodes(path) | n.name] AS node_names,
                    [r IN relationships(path) | type(r)] AS rel_types
                LIMIT 3
                """
                try:
                    records = await self.knowledge_graph.execute_cypher(
                        cypher, {"name_a": entities[i], "name_b": entities[j]}
                    )
                    for rec in records:
                        nodes = rec.get("node_names", [])
                        rels = rec.get("rel_types", [])
                        path_str = ""
                        for k, node in enumerate(nodes):
                            path_str += node
                            if k < len(rels):
                                path_str += f" --[{rels[k]}]--> "
                        contexts.append(GraphRAGContext(
                            content=f"推理路径: {path_str}",
                            source_type="path",
                            score=0.85,
                            metadata={"from": entities[i], "to": entities[j]},
                        ))
                except Exception:
                    continue
        return contexts

    # ── Step 5: 社区摘要 ─────────────────────────────────────

    async def _community_summary(self, subgraph_results: list[GraphRAGContext]) -> GraphRAGContext:
        """对检索到的子图信息进行摘要"""
        subgraph_text = "\n".join(r.content for r in subgraph_results[:20])
        messages = [
            SystemMessage(content=COMMUNITY_SUMMARY_PROMPT),
            HumanMessage(content=f"子图信息:\n{subgraph_text}"),
        ]
        resp = await self.llm.ainvoke(messages)
        return GraphRAGContext(
            content=resp.content,
            source_type="community",
            score=0.9,
            metadata={"type": "community_summary"},
        )

    # ── Step 6: 交叉重排序 ───────────────────────────────────

    @staticmethod
    def _cross_rerank(contexts: list[GraphRAGContext], query: str) -> list[GraphRAGContext]:
        """
        交叉重排序策略:
          - 向量检索: 基础分 × 1.0
          - 子图检索: 基础分 × 1.15 (结构化信息更精准)
          - 路径检索: 基础分 × 1.25 (推理链最有价值)
          - 社区摘要: 基础分 × 1.1  (高层概览)
        """
        weight_map = {"vector": 1.0, "subgraph": 1.15, "path": 1.25, "community": 1.1}
        for ctx in contexts:
            ctx.score *= weight_map.get(ctx.source_type, 1.0)

        seen: set[str] = set()
        unique: list[GraphRAGContext] = []
        for ctx in contexts:
            key = ctx.content[:80]
            if key not in seen:
                seen.add(key)
                unique.append(ctx)

        unique.sort(key=lambda c: c.score, reverse=True)
        return unique
