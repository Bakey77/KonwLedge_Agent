# 图谱检索返回 0 条问题修复日志

**日期**: 2026-04-12
**问题**: 图谱检索返回 0 条，但 Neo4j 中有实体数据
**状态**: ✅ 部分修复（Schema 问题已解决，实体名匹配问题待处理）

---

## 一、问题现象

1. 问答接口 `POST /api/qa/ask` 调用时，图谱检索始终返回 0 条
2. `reasoning_steps` 显示：`图谱检索: 0 条`
3. Neo4j 中实际有 126 个实体、137 条关系
4. 直接查询 Neo4j：`SR8000` 有 30 个邻居，`圣锐电吹管-SR9000` 有 10 个邻居

---

## 二、排查过程

### 错误假设（最初）
怀疑是 **Entity Linking 实体名不匹配**：
- 查询用 "SR9000"，图里存的是 "圣锐电吹管-SR9000"
- exact match 查不到

### 进一步排查发现真正问题
在添加调试日志后，发现：

```
entities extracted: ['SR8000', 'SR9000']  ✅ 实体链接正常
LLM 生成的 Cypher 使用错误标签:
  MATCH (sr8000:Product {name: 'SR8000'})...     ❌ 不存在 :Product 标签
  MATCH (sr9000:Technology {name: 'SR9000'})...  ❌ 不存在 :Technology 标签
```

**真正根因**: `CYPHER_GENERATION_PROMPT` 告诉 LLM 用 `Person/Product/Technology/...` 标签，但实际 schema 所有节点都是 `:Entity`。

---

## 三、实际根因（两个 Bug）

### Bug 1: Schema 不匹配（已修复）

**`qa_agent.py:68-78` 旧的错误提示**:
```
节点标签: Person, Organization, Technology, Product, Concept, Location
关系类型: belongs_to, works_at, related_to, uses...
```

**`knowledge_graph.py` 实际的 schema**:
```python
# 所有节点统一标签 :Entity
MERGE (e:Entity {name: $name})

# 关系类型全部转大写+下划线
rel_type = relation.relation.upper().replace(" ", "_")  # → RELATED_TO, USES, ...
```

**结果**: LLM 生成的 Cypher 使用 `:Product` / `:Technology` 等不存在的标签，执行查空，异常被静默吞掉。

### Bug 2: 异常静默吞掉（已修复）

**旧的异常处理**:
```python
except Exception:
    continue  # 所有失败被忽略，无日志
```

**修复后**:
```python
except Exception as e:
    print(f"[WARN] Graph retrieval cypher failed: cypher={cypher}, error={e}")
    continue
```

---

## 四、修复内容

### 1. 修复 `qa_agent.py` 的 `CYPHER_GENERATION_PROMPT`

**修改前**:
```python
CYPHER_GENERATION_PROMPT = """\
知识图谱 Schema:
- 节点标签: Person, Organization, Technology, Product, Concept, Location
- 关系类型: belongs_to, works_at, located_in, developed_by, related_to...
"""
```

**修改后**:
```python
CYPHER_GENERATION_PROMPT = """\
知识图谱 Schema（注意：所有节点都是 :Entity 标签，不要用其他标签！）:
- 节点标签: :Entity（所有节点统一标签，不要使用 Person/Product/Technology 等）
- 关系类型: RELATED_TO, USES, BELONGS_TO, DEVELOPS, LOCATED_IN 等（关系类型全部大写下划线）
- 节点属性: name (string), type (string), description (string), source (string)

示例（查 SR8000 的邻居）:
{"queries": ["MATCH (e:Entity {name: 'SR8000'})-[r]-(neighbor) RETURN ... "]}
"""
```

### 2. 添加调试日志

在 Cypher 执行失败时打印警告，而不是静默忽略。

---

## 五、验证结果

修复后测试 `SR8000和SR9000的音色差别`：

```json
{
  "reasoning_steps": [
    "识别问题意图: comparative",
    "检索到 8 条相关上下文",
    "向量检索: 0 条",
    "图谱检索: 8 条",  // ✅ 从 0 变成 8
    "答案生成完成"
  ],
  "sources": [
    {"source": "knowledge_graph", "content": "{'source': 'SR8000', 'relation': 'RELATED_TO', 'target': '交响乐套鼓'}"},
    {"source": "knowledge_graph", "content": "{'source': 'SR8000', 'relation': 'RELATED_TO', 'target': '民族弹拨乐'}"},
    ...
  ]
}
```

**SR8000 图谱检索成功**，返回 8 条关联关系。

---

## 六、遗留问题：实体名不完全匹配

### 问题描述

- **Neo4j 存储的实体名**: `圣锐电吹管-SR9000`（完整名称）
- **查询时提取的实体名**: `SR9000`（简称）
- **结果**: exact match 查不到 SR9000

```
# 能查到 ✅
MATCH (e:Entity {name: 'SR8000'})-[r]-(neighbor)  # 图里存的就是 'SR8000'

# 查不到 ❌
MATCH (e:Entity {name: 'SR9000'})-[r]-(neighbor)  # 图里存的是 '圣锐电吹管-SR9000'
```

### 根本原因

**实体链接阶段**：LLM 从 query 提取出 `SR9000`，但知识抽取阶段存储的是产品全名 `圣锐电吹管-SR9000`。

### 解决方案（待实施）

1. **方案 A - 实体名标准化**：在 `knowledge_extract_agent` 存储时，将简称（如 `SR9000`）作为 `name` 字段，全名作为 `description`
2. **方案 B - 模糊匹配**：修改 `get_neighbors` 使用 `CONTAINS` 而非 exact match
   ```cypher
   MATCH (e:Entity) WHERE e.name CONTAINS $name
   ```
3. **方案 C - 实体别名**：存储时同时保存 `name` 和 `aliases: ['SR9000', 'SR-9000']`，检索时查询 aliases

---

## 七、经验教训

1. **静默吞异常是万恶之源**：没有日志导致排查花了很长时间
2. **Schema 文档必须与实际一致**：LLM 生成的查询完全依赖 prompt 中的 schema 描述
3. **Entity Linking 问题是 RAG 经典难题**：query 中的名称与知识库中的名称不一致
4. **先验证假设再修复**：最初以为是 SDK 兼容性，实际是网络；最初以为是实体名不匹配，实际还有 schema 不匹配叠加

---

## 八、修复文件清单

| 文件 | 修改内容 |
|------|---------|
| `python/agents/qa_agent.py` | 修复 `CYPHER_GENERATION_PROMPT` schema 描述；添加异常日志 |
