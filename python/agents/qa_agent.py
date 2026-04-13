"""
问答 Agent — 混合检索 (Vector + Graph) + 多跳推理 + 答案生成

核心能力:
  1. 意图识别 & 查询改写
  2. 向量检索 (语义相似度)
  3. 图谱检索 (Cypher 查询 / 子图遍历)
  4. 混合排序 & 重排序
  5. 基于检索结果的答案生成（带引用来源）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from config import settings


class QueryIntent(str, Enum):
    FACTOID = "factoid"           # 事实型问题
    ANALYTICAL = "analytical"     # 分析型问题
    COMPARATIVE = "comparative"   # 对比型问题
    PROCEDURAL = "procedural"     # 流程型问题
    EXPLORATORY = "exploratory"   # 探索型问题


@dataclass
class RetrievedContext:
    content: str
    source: str
    score: float
    retrieval_type: str  # "vector" | "graph" | "hybrid"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class QAResult:
    question: str
    answer: str
    contexts: list[RetrievedContext]
    intent: QueryIntent
    confidence: float
    reasoning_steps: list[str] = field(default_factory=list)


INTENT_PROMPT = """\
你是一个查询意图分类器。根据用户问题，返回意图类别（只返回类别名）：
- factoid: 事实型（谁/什么/哪里/何时）
- analytical: 分析型（为什么/怎么理解）
- comparative: 对比型（A和B有什么区别）
- procedural: 流程型（怎么做/步骤）
- exploratory: 探索型（有哪些/概述）
"""

QUERY_REWRITE_PROMPT = """\
你是一个查询改写专家。将用户问题改写为更适合检索的形式。
要求：
1. 提取核心实体和关键词
2. 生成 1-3 个检索查询
3. 返回 JSON: {"queries": ["查询1", "查询2"], "entities": ["实体1"], "keywords": ["关键词1"]}
"""

CYPHER_GENERATION_PROMPT = """\
你是一个 Neo4j Cypher 查询生成专家。根据用户问题和提取的实体，生成 Cypher 查询。

知识图谱 Schema（注意：所有节点都是 :Entity 标签，不要用其他标签！）:
- 节点标签: :Entity（所有节点统一标签，不要使用 Person/Product/Technology 等）
- 关系类型: RELATED_TO, USES, BELONGS_TO, DEVELOPS, LOCATED_IN 等（关系类型全部大写下划线）
- 节点属性: name (string), type (string), description (string), source (string)

生成 1-2 条 Cypher 查询，返回 JSON: {"queries": ["MATCH ...", "MATCH ..."]}
只返回 JSON，不要其他文字。

示例（查 SR8000 的邻居）:
{"queries": ["MATCH (e:Entity {name: 'SR8000'})-[r]-(neighbor) RETURN e.name AS source, type(r) AS relation, neighbor.name AS target, neighbor.type AS target_type LIMIT 20"]}"""

ANSWER_PROMPT = """\
你是一个专业的企业知识问答助手。根据检索到的上下文信息回答用户问题。

要求：
1. 答案必须基于提供的上下文，不要编造
2. 如果上下文信息不足，明确告知用户
3. 引用信息来源（如 [来源: xxx]）
4. 如果涉及多个信息源，综合分析后给出结论
5. 保持专业、准确、简洁
"""


class QAAgent:
    """
    问答 Agent

    工作流:
      query → intent_classify → rewrite → parallel_retrieve → rerank → generate_answer
    """

    def __init__(
        self,
        vector_store: Any = None,
        knowledge_graph: Any = None,
    ) -> None:
        self.llm = ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            temperature=0,
        )
        self.vector_store = vector_store
        self.knowledge_graph = knowledge_graph

    # ── public API ───────────────────────────────────────────

    async def answer(self, question: str) -> QAResult:
        """完整问答流程"""
        intent = await self._classify_intent(question)
        rewritten = await self._rewrite_query(question)

        vector_contexts = await self._vector_retrieve(rewritten)
        graph_contexts = await self._graph_retrieve(question, rewritten)

        all_contexts = self._hybrid_rerank(vector_contexts + graph_contexts)
        top_contexts = all_contexts[:8]

        answer_text, reasoning = await self._generate_answer(question, top_contexts, intent)

        return QAResult(
            question=question,
            answer=answer_text,
            contexts=top_contexts,
            intent=intent,
            confidence=self._calc_confidence(top_contexts),
            reasoning_steps=reasoning,
        )

    # ── intent classification ────────────────────────────────

    async def _classify_intent(self, question: str) -> QueryIntent:
        messages = [
            SystemMessage(content=INTENT_PROMPT),
            HumanMessage(content=question),
        ]
        resp = await self.llm.ainvoke(messages)
        raw = resp.content.strip().lower()
        for intent in QueryIntent:
            if intent.value in raw:
                return intent
        return QueryIntent.FACTOID

    # ── query rewriting ──────────────────────────────────────

    async def _rewrite_query(self, question: str) -> dict:
        import json
        messages = [
            SystemMessage(content=QUERY_REWRITE_PROMPT),
            HumanMessage(content=question),
        ]
        resp = await self.llm.ainvoke(messages)
        try:
            cleaned = resp.content.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
            return json.loads(cleaned)
        except (json.JSONDecodeError, IndexError):
            return {"queries": [question], "entities": [], "keywords": []}

    # ── vector retrieval ─────────────────────────────────────

    async def _vector_retrieve(self, rewritten: dict) -> list[RetrievedContext]:
        if not self.vector_store:
            return []

        contexts: list[RetrievedContext] = []
        for query in rewritten.get("queries", []):
            results = await self.vector_store.search(query, top_k=5)
            for doc, score in results:
                contexts.append(RetrievedContext(
                    content=doc.get("content", ""),
                    source=doc.get("source", "vector_store"),
                    score=score,
                    retrieval_type="vector",
                    metadata=doc.get("metadata", {}),
                ))
        return contexts

    # ── graph retrieval ──────────────────────────────────────

    async def _graph_retrieve(self, question: str, rewritten: dict) -> list[RetrievedContext]:
        if not self.knowledge_graph:
            return []

        raw_entities = rewritten.get("entities", [])
        resolved_entities = await self._resolve_graph_entities(raw_entities)

        contexts: list[RetrievedContext] = []
        contexts.extend(await self._retrieve_neighbors_contexts(resolved_entities))
        contexts.extend(await self._retrieve_generated_cypher(question, resolved_entities or raw_entities))
        return contexts

    async def _resolve_graph_entities(self, entities: list[str]) -> list[str]:
        """将改写实体映射到图谱中的实际实体名（优先精确，其次模糊）。"""
        resolved: list[str] = []
        seen: set[str] = set()

        def _add(name: str) -> None:
            if name and name not in seen:
                seen.add(name)
                resolved.append(name)

        for entity in entities:
            name = entity.strip()
            if not name:
                continue

            try:
                exact = await self.knowledge_graph.get_entity(name)
                if exact:
                    _add(name)
            except Exception as e:
                print(f"[WARN] Graph entity exact match failed: entity={name}, error={e}")

            try:
                candidates = await self.knowledge_graph.search_entities(name, limit=10)
                for item in candidates:
                    candidate_name = item.get("name", "")
                    if candidate_name and (name in candidate_name or candidate_name in name):
                        _add(candidate_name)
            except Exception as e:
                print(f"[WARN] Graph entity fuzzy match failed: entity={name}, error={e}")

        return resolved

    async def _retrieve_neighbors_contexts(self, entities: list[str], hops: int = 2) -> list[RetrievedContext]:
        """确定性图谱召回：按实体取邻居，作为图谱检索主链路。"""
        contexts: list[RetrievedContext] = []
        for entity_name in entities:
            try:
                records = await self.knowledge_graph.get_neighbors(entity_name, hops=hops)
            except Exception as e:
                print(f"[WARN] Graph neighbors retrieval failed: entity={entity_name}, error={e}")
                continue

            for record in records:
                relation_chain = " -> ".join(record.get("relations", []))
                content = (
                    f"{record.get('source', '')} "
                    f"--[{relation_chain}]--> "
                    f"{record.get('target', '')} "
                    f"({record.get('target_type', '')}): "
                    f"{record.get('target_desc', '')}"
                )
                contexts.append(RetrievedContext(
                    content=content,
                    source="knowledge_graph",
                    score=0.82,
                    retrieval_type="graph",
                    metadata={"entity": entity_name, "strategy": "neighbors", "hops": hops},
                ))
        return contexts

    @staticmethod
    def _is_schema_aligned_cypher(cypher: str) -> bool:
        """最小化校验，避免执行明显偏离当前图谱 schema 的查询。"""
        up = cypher.upper()
        if "MATCH" not in up:
            return False
        if ":ENTITY" not in up:
            return False
        # 当前图谱结构是统一 :Entity，不接受其他节点标签。
        disallowed = [":PERSON", ":ORGANIZATION", ":PRODUCT", ":TECHNOLOGY", ":CONCEPT", ":LOCATION"]
        if any(tag in up for tag in disallowed):
            return False
        return True

    async def _retrieve_generated_cypher(self, question: str, entities: list[str]) -> list[RetrievedContext]:
        """LLM 生成 Cypher 的补充召回路径。"""
        import json

        messages = [
            SystemMessage(content=CYPHER_GENERATION_PROMPT),
            HumanMessage(content=f"问题: {question}\n实体: {entities}"),
        ]
        resp = await self.llm.ainvoke(messages)
        try:
            cleaned = resp.content.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
            cypher_data = json.loads(cleaned)
        except (json.JSONDecodeError, IndexError) as ex:
            print(f"[WARN] Graph retrieve - cypher parsing failed: {ex}, raw: {resp.content[:200]}")
            return []

        contexts: list[RetrievedContext] = []
        for cypher in cypher_data.get("queries", []):
            if not self._is_schema_aligned_cypher(cypher):
                print(f"[WARN] Skip schema-mismatched cypher: {cypher}")
                continue
            try:
                records = await self.knowledge_graph.execute_cypher(cypher)
                for record in records:
                    contexts.append(RetrievedContext(
                        content=str(record),
                        source="knowledge_graph",
                        score=0.8,
                        retrieval_type="graph",
                        metadata={"cypher": cypher, "strategy": "llm_cypher"},
                    ))
            except Exception as e:
                print(f"[WARN] Graph retrieval cypher failed: cypher={cypher}, error={e}")
                continue
        return contexts

    # ── hybrid reranking ─────────────────────────────────────

    @staticmethod
    def _hybrid_rerank(contexts: list[RetrievedContext]) -> list[RetrievedContext]:
        """
        混合重排序：向量分数 + 图谱分数加权
        图谱检索结果天然带有结构化关系，给予略高权重
        """
        weight_map = {"vector": 1.0, "graph": 1.2, "hybrid": 1.1}
        for ctx in contexts:
            ctx.score *= weight_map.get(ctx.retrieval_type, 1.0)

        seen: set[str] = set()
        unique: list[RetrievedContext] = []
        # 图谱邻居结果：按实体分桶，每实体最多 MAX_PER_ENTITY 条，保证多样性
        MAX_PER_ENTITY = 4
        entity_bucket: dict[str, list[RetrievedContext]] = {}
        for ctx in contexts:
            if ctx.retrieval_type == "graph" and ctx.metadata.get("strategy") == "neighbors":
                entity = ctx.metadata.get("entity", "")
                target = ""
                parts = ctx.content.split("-->", 1)
                if len(parts) > 1:
                    target = parts[1].split("(")[0].strip()
                key = f"graph:{entity}:{target}"
                if entity not in entity_bucket:
                    entity_bucket[entity] = []
                # 内容去重 + 每实体上限
                if key not in seen and len(entity_bucket[entity]) < MAX_PER_ENTITY:
                    seen.add(key)
                    entity_bucket[entity].append(ctx)
            else:
                key = ctx.content[:100]
                if key not in seen:
                    seen.add(key)
                    unique.append(ctx)

        # 合并各实体bucket结果
        for bucket in entity_bucket.values():
            unique.extend(bucket)

        unique.sort(key=lambda c: c.score, reverse=True)
        return unique

    # ── answer generation ────────────────────────────────────

    async def _generate_answer(
        self,
        question: str,
        contexts: list[RetrievedContext],
        intent: QueryIntent,
    ) -> tuple[str, list[str]]:
        context_text = "\n\n".join(
            f"[来源 {i+1}: {c.source} | 类型: {c.retrieval_type} | 分数: {c.score:.2f}]\n{c.content}"
            for i, c in enumerate(contexts)
        )
        reasoning_steps = [
            f"识别问题意图: {intent.value}",
            f"检索到 {len(contexts)} 条相关上下文",
            f"向量检索: {sum(1 for c in contexts if c.retrieval_type == 'vector')} 条",
            f"图谱检索: {sum(1 for c in contexts if c.retrieval_type == 'graph')} 条",
        ]

        messages = [
            SystemMessage(content=ANSWER_PROMPT),
            HumanMessage(content=f"上下文信息:\n{context_text}\n\n用户问题: {question}"),
        ]
        resp = await self.llm.ainvoke(messages)
        reasoning_steps.append("答案生成完成")
        return resp.content, reasoning_steps

    @staticmethod
    def _calc_confidence(contexts: list[RetrievedContext]) -> float:
        if not contexts:
            return 0.0
        avg_score = sum(c.score for c in contexts) / len(contexts)
        return min(avg_score, 1.0)
