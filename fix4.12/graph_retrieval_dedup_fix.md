# 图谱检索结果单一实体垄断问题修复日志

**日期**: 2026-04-12
**问题**: 图谱检索结果被 SR8000 垄断，SR6000 的邻居记录全部被过滤
**状态**: ✅ 已修复

---

## 一、问题现象

### 修复 Schema Bug 后的查询结果

测试查询 `SR8000和SR6000的音色有什么差别`：

```json
{
  "reasoning_steps": [
    "识别问题意图: comparative",
    "检索到 8 条相关上下文",
    "向量检索: 0 条",
    "图谱检索: 8 条",  // 全部是 SR8000 的邻居，SR6000 为 0
    "答案生成完成"
  ],
  "sources": [
    "SR8000 --[USES]--> 电容触摸技术",
    "SR8000 --[USES]--> 专利虹音技术",
    "SR8000 --[PART_OF]--> 1.7 寸 LCD 中文显示屏",
    "SR8000 --[PART_OF]--> 13W 双扬声器",
    "SR8000 --[RELATED_TO]--> 圣锐第二代专业音色库",
    "SR8000 --[RELATED_TO -> BELONGS_TO]--> 圣锐",
    "SR8000 --[RELATED_TO -> USES]--> SR8000",  // 自环
    "SR8000 --[PART_OF]--> MEE 精密轴承"
  ]
}
```

**所有 8 条图谱检索结果都是 SR8000 的邻居，SR6000 的 0 条**。

---

## 二、排查过程

### 1. 添加 DEBUG 日志定位

在 `_resolve_graph_entities` 和 `_retrieve_neighbors_contexts` 添加日志：

```python
async def _resolve_graph_entities(self, entities: list[str]) -> list[str]:
    print(f"[DEBUG] _resolve_graph_entities input: {entities}")
    ...

async def _retrieve_neighbors_contexts(self, entities: list[str], hops: int = 2):
    for entity_name in entities:
        records = await self.knowledge_graph.get_neighbors(entity_name, hops=hops)
        print(f"[DEBUG] got {len(records)} records for {entity_name}")
        for record in records:
            ...
```

### 2. DEBUG 日志揭示真相

```
_resolve_graph_entities input:  ['SR8000', 'SR6000']    ✅ 实体提取正确
_resolve_graph_entities output: ['SR8000', 'SR6000']    ✅ 实体解析正确
_retrieve_neighbors_contexts querying: SR8000 → got 19 records
_retrieve_neighbors_contexts querying: SR6000 → got 39 records   ✅ SR6000 有 39 条邻居
```

**结论**：
- `_resolve_graph_entities` 正确解析了 SR8000 和 SR6000
- `_retrieve_neighbors_contexts` 正确查到了 SR6000 的 39 条邻居记录
- 但最终只返回了 8 条，全是 SR8000 的

### 3. 问题定位：`_hybrid_rerank` 的 deduplication

在 `_hybrid_rerank` 添加日志：

```python
unique.sort(key=lambda c: c.score, reverse=True)
print(f"[DEBUG] _hybrid_rerank: before={len(contexts)}, after={len(unique)}")
```

输出：
```
_hybrid_rerank: before=74, after=54
```

58 条图谱记录只被 dedup 掉了 20 条，不应该只剩 8 条全是 SR8000。说明问题不在 deduplication 数量，而在 **top_k 截断**。

### 4. 进一步分析 dedup 日志

添加更细粒度的 dedup 日志：

```python
if key not in seen:
    seen.add(key)
    unique.append(ctx)
else:
    print(f"[DEBUG] dedup removed: key={key}, content={ctx.content[:60]}")
```

**关键发现**：dedup 日志显示 SR6000 的邻居记录被大量 dedup 掉，但 dedup key 重复率高并非原因。真正问题在于 **排序后取 top_k=8 时，SR8000 的记录因 score 相同或更早出现而占据前 8 条**。

同时发现：
- SR6000 的多跳关系（2 hops）导致同一 target（如 `电子乐器`）出现多次
- 同一 target 的多条记录因 `content[:100]` 相同而被 dedup
- **SR6000 的关系链短（多是 USES->USES），score 不如 SR8000 的一跳关系高**

---

## 三、问题根因

**分桶前的 deduplication key 粒度不够**：

```python
# 旧的 dedup key：内容前 100 字符
key = ctx.content[:100]
```

对于结构相似的内容（如 `SR6000 --[USES]--> 电子乐器` vs `SR6000 --[USES -> USES]--> 电子乐器`），前 100 字符相同，导致误去重。

加上 **top_k=8 截断**后，排序分高的 SR8000 记录占据了全部 8 个位置，SR6000 被完全挤出。

---

## 四、修复方案

**按实体分桶 + 每实体上限 + 内容去重**：

```python
MAX_PER_ENTITY = 4  # 每实体最多保留 4 条
entity_bucket: dict[str, list[RetrievedContext]] = {}

for ctx in contexts:
    if ctx.retrieval_type == "graph" and ctx.metadata.get("strategy") == "neighbors":
        entity = ctx.metadata.get("entity", "")
        target = ""
        parts = ctx.content.split("-->", 1)
        if len(parts) > 1:
            target = parts[1].split("(")[0].strip()
        key = f"graph:{entity}:{target}"  # 细粒度去重 key

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

# 合并各实体 bucket 结果
for bucket in entity_bucket.values():
    unique.extend(bucket)

unique.sort(key=lambda c: c.score, reverse=True)
```

**关键改进**：
1. **去重 key 细化**：`graph:{entity}:{target}` 而非 `content[:100]`
2. **每实体上限**：每实体最多 4 条，保证多样性
3. **分桶合并**：不同实体的邻居各自独立，不会互相挤占

---

## 五、验证结果

修复后测试 `SR8000和SR6000的音色有什么差别`：

```json
{
  "reasoning_steps": [
    "识别问题意图: comparative",
    "检索到 8 条相关上下文",
    "向量检索: 0 条",
    "图谱检索: 8 条",
    "答案生成完成"
  ],
  "sources": [
    "SR8000 --[USES]--> 电容触摸技术 (Technology)",
    "SR8000 --[USES]--> 专利虹音技术 (Technology)",
    "SR8000 --[PART_OF]--> 1.7 寸 LCD 中文显示屏 (Product)",
    "SR8000 --[PART_OF]--> 13W 双扬声器 (Product)",
    "SR6000 --[USES]--> 4 核 DSP 音源系统 (Technology)",   // ✅ SR6000 出现
    "SR6000 --[PART_OF]--> 金属触摸滚轮 (Technology)",    // ✅
    "SR6000 --[RELATED_TO]--> 民族音色 (Concept)",        // ✅
    "SR6000 --[RELATED_TO -> USES]--> 电子乐器 (Product)" // ✅
  ]
}
```

**4 条 SR8000 + 4 条 SR6000 = 8 条，每实体各 4 条**，多样性正常。

---

## 六、修复文件清单

| 文件 | 修改内容 |
|------|---------|
| `python/agents/qa_agent.py` | `_hybrid_rerank` 改为按实体分桶 + 每实体上限的去重策略 |

---

## 七、经验教训

1. **dedup key 粒度决定去重质量**：用 `content[:100]` 对结构化内容会误去重，应结合 metadata（entity、target）做细粒度去重
2. **top_k 截断 + 相同分数 = 饥饿问题**：若不做分桶，多实体查询时高分数实体会垄断所有 top_k 位置
3. **多跳关系放大 dedup 问题**：2 hops 查到的多跳路径内容相似度高，更容易被 dedup 误杀
4. **分桶策略是解决多实体检索多样性的有效方法**：类似搜索引擎的 diversity ranking
