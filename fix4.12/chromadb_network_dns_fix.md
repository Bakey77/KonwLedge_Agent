# ChromaDB DNS/网络问题修复日志

**日期**: 2026-04-12
**问题**: API 容器无法解析 ChromaDB 主机名
**状态**: ✅ 已修复

---

## 一、问题现象

1. API 日志报错：`httpx.ConnectError: [Errno -2] Name or service not known`
2. API 容器内执行 `getent hosts chromadb` 返回 `DNS_FAIL`
3. ChromaDB 服务本体健康：`curl localhost:8000/api/v2/heartbeat` 正常返回

---

## 二、根本原因

**Docker 网络配置错误 - API 和 ChromaDB 不在同一网络**

### 网络拓扑（修复前）
```
agenthub-api      → agent-knowledge-hub-main_default 网络 (DNS别名: api, agenthub-api)
agenthub-chromadb → bridge 网络 (默认网络，DNS别名: agenthub-chromadb)

原因：agenthub-chromadb 是手动 docker run 创建的，未加入 compose 网络
```

### DNS 解析情况
| 主机名 | agenthub-api 内解析结果 |
|--------|------------------------|
| `chromadb` | ❌ DNS_FAIL |
| `agenthub-chromadb` | ❌ DNS_FAIL |
| `agenthub-neo4j` | ✅ 172.18.0.4 (同一网络) |

---

## 三、修复步骤

### 1. 删除手动创建的 ChromaDB 容器
```bash
docker stop agenthub-chromadb
docker rm agenthub-chromadb
```

### 2. 让 docker-compose 管理 ChromaDB（自动加入同一网络）
```bash
docker-compose up -d chromadb
```

### 3. 验证网络和 DNS
```bash
docker exec agenthub-api getent hosts chromadb
# 返回: 172.18.0.6 chromadb

docker exec agenthub-api getent hosts agenthub-chromadb
# 返回: 172.18.0.6 agenthub-chromadb
```

### 4. 重启 API 容器加载新配置
```bash
docker-compose up -d --force-recreate api
```

---

## 四、DNS 别名说明

docker-compose 创建的 `chromadb` 服务，在 `agent-knowledge-hub-main_default` 网络中有两个 DNS 别名：

| 别名 | 来源 |
|------|------|
| `chromadb` | docker-compose 服务名 |
| `agenthub-chromadb` | 容器名（项目名前缀） |

两者均可解析，`.env` 配置的 `CHROMA_HOST=agenthub-chromadb` 和 `CHROMA_HOST=chromadb` 都可以正常工作。

---

## 五、验证结果

```bash
curl http://localhost:8080/api/admin/stats

# 返回:
{
  "vector_store": {
    "backend": "chroma",
    "total_vectors": 0,
    "collection": "knowledge_chunks"
  },
  "knowledge_graph": {
    "total_entities": 0,
    "total_relations": 0
  }
}
```

---

## 六、总结

| 问题 | 根因 | 解决方案 |
|------|------|---------|
| API 无法连接 ChromaDB | 手动创建的容器在不同网络 | 使用 docker-compose 管理 |
| DNS 解析失败 | 容器不在同一网络 | 确保使用 docker-compose networks |
| env 配置不一致 | `.env` 用 `chromadb`，但服务名配置不一致 | 统一使用 docker-compose 管理 |

---

## 七、经验教训

1. **不要手动创建已在 docker-compose 中定义的服务容器**
2. **docker-compose 的 `depends_on` 只保证启动顺序，不自动创建网络**
3. **所有服务都应该在 docker-compose.yml 中统一定义**
