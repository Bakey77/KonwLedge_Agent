# 检索评估框架 - 快速参考

> 为你的RAG系统设计的4指标评估框架 | 支持向量检索和图谱检索对比

## 🎯 核心指标速记

| 指标 | 说明 | 目标值 | 何时优先 |
|------|------|--------|---------|
| **Recall@K** | Top-K中找到相关文档的比例 | ≥ 0.85 | 用户关心"找全" |
| **MRR** | 第一个相关文档排名倒数 | ≥ 0.70 | 用户只看前几个 |
| **NDCG@K** | 综合排名质量 | ≥ 0.75 | 用户看整个列表 |
| **Hit Rate@K** | 有多少查询至少找到一个相关 | ≥ 0.85 | 系统整体可用性 |

---

## 🚀 快速开始

### 1️⃣ 运行示例评估（含mock数据）
```bash
cd /Users/liaomeiqi/Downloads/agent-knowledge-hub-main
python python/eval/example_eval.py
```

预期输出：
```
✅ 加载了 26 个测试用例

向量检索 @5:
  Recall: 0.508 (找到50%的相关文档)
  MRR: 0.654    (第一个相关平均在第1.5位)
  Hit Rate: 65.4% (65%的查询能找到相关)
```

### 2️⃣ 查看对比分析
```bash
python python/eval/comparison_analysis.py
```

输出向量检索 vs 图谱检索的对比表，以及分解分析。

### 3️⃣ 集成真实检索
修改 `python/eval/example_eval.py` 中的两个函数：

```python
# 改这两个函数
def simulate_vector_retrieval(query, relevant_chunks, k):
    # 原来: results = []  # mock
    # 改为: results = await vector_store.search(query, top_k=k)
    ...

def simulate_graph_retrieval(query, relevant_chunks, k):
    # 改为: results = await knowledge_graph.query(query, top_k=k)
    ...
```

---

## 📊 文件结构

```
python/eval/
├── __init__.py                 # 包初始化
├── metrics.py                  # ⭐ 核心计算引擎
│   ├── MetricsCalculator       # 计算Recall/MRR/NDCG/Hit
│   ├── RetrievalMetrics        # 单个查询的结果
│   └── EvaluationReporter      # 汇总和报告
├── retrieval_eval.py           # 检索评估框架
├── example_eval.py             # ⭐ 可直接运行的示例
├── comparison_analysis.py       # 对比分析脚本
└── README.md                   # 详细文档
```

---

## 💡 关键概念

### Recall@K vs Hit Rate@K
- **Recall**: 单个查询角度，相关文档找得有多全
- **Hit Rate**: 整体角度，多少查询能至少找到一个

### MRR=0.5 是什么意思？
= 第一个相关文档排在第2位（因为 1/2 = 0.5）

### NDCG 何时重要？
相关性有高低之分时。如果所有相关文档等权重，NDCG ≈ Recall。

---

## 📈 解读结果

### ✅ 指标都很好：Recall≥0.85, Hit≥0.9, MRR≥0.7
→ 系统已可用于生产

### ⚠️ Recall低但Hit高
→ 虽然不够完全，但用户能至少找到一个相关
→ 优先改进排序而非召回

### ⚠️ MRR低但Recall高
→ 找全了，但排序不对
→ 需要重排序算法

### ❌ 所有指标都差
→ 检索本身有问题
→ 检查：查询改写、embedding模型、知识库

---

## 🔧 常见优化

| 问题 | 可能原因 | 解决方案 |
|------|--------|---------|
| Recall 低 | 查询改写不好 | 改进 LLM prompt 或使用多个改写 |
| Recall 低 | Embedding 模型差 | 升级到 text-embedding-3-large |
| MRR 低 | 排序不对 | 使用重排模型 (reranker) |
| Hit Rate 低 | 某类问题很差 | 针对性改进该类问题的检索 |

---

## 📝 实战示例

```python
from python.eval.metrics import MetricsCalculator

# 你的检索结果
retrieved = [
    {"id": "chunk-4", "score": 0.92},
    {"id": "chunk-5", "score": 0.85},
]

# Ground truth
relevant_ids = ["chunk-4", "chunk-5"]

# 计算所有指标
result = MetricsCalculator.evaluate_single_query(
    query_id="q_1",
    query="试用期离职规定",
    retrieved_docs=retrieved,
    relevant_doc_ids=relevant_ids,
    k=5
)

print(f"Recall:     {result.recall:.1%}  ✅ 完全正确")
print(f"MRR:        {result.mrr:.3f}    ✅ 第一个都对")
print(f"Hit:        {result.hit}       ✅ 有相关文档")
```

---

## 📋 检查清单

- [ ] 数据集是否有地truth chunks标注？
- [ ] 运行了 `example_eval.py` 看到了结果？
- [ ] 用对比分析查看了向量 vs 图谱？
- [ ] 诊断报告中哪些查询Recall为0？
- [ ] 是否在特定难度/类型上表现差？
- [ ] 集成了真实向量存储或知识图谱？

---

## 🎓 更多资源

- **详细指南**: 见 `RAG_EVALUATION_GUIDE.md`
- **4指标说明**: 见 `python/eval/README.md`
- **数据集**: `employee_handbook_qa.json`（26个测试用例）
- **核心实现**: `python/eval/metrics.py`

---

## ❓ FAQ

**Q: 我应该优先看哪个指标？**
A: 依靠业务需求：
- 用户只看前3个 → 优先 MRR
- 用户看整页结果 → 优先 NDCG
- 通常 → Recall 和 Hit Rate

**Q: Recall@5 vs Recall@10？**
A: 前者更严格。建议都评估，看K值对结果的敏感度。

**Q: 为什么同个查询的Recall会不同？**
A: 因为Top-K不同。K越大，Recall通常越高。

**Q: 怎样判断模型是否足够好？**
A: 对标竞品或业界水准，通常 Recall@5≥0.85 为优秀。

---

**最后更新**: 2026-04-13 | **维护者**: 提示框架
