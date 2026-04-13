# Watchdog 自动监控功能问题日志

**日期**: 2026-04-12
**问题**: Watchdog 代码已实现但未正常启动
**状态**: 已修复

---

## 一、问题现象

1. 项目中 `knowledge_update_agent.py` 已实现 `start_watching()` 方法
2. 但 API 启动后，文件监控功能未生效
3. 在 `python/uploads/` 目录下创建/修改文件后，Neo4j 未自动更新

---

## 二、问题原因

### 原因 1：update_agent 实例未暴露

**代码位置**: `orchestrator/graph.py`

```python
# 原代码
def build_knowledge_graph_workflow(...) -> dict[str, Any]:
    update_agent = KnowledgeUpdateAgent(...)  # 局部变量，外部无法访问
    return {
        "ingest": ...,
        "qa": ...,
        "update": ...
    }
```

**问题**: `update_agent` 是函数内部的局部变量，API 无法获取实例来调用 `start_watching()`

---

### 原因 2：启动时未调用 start_watching()

**代码位置**: `api/main.py`

```python
# 原代码
workflows.update(
    build_knowledge_graph_workflow(...)  # 只接收返回值，不关心 update_agent
)
```

**问题**: 即使 `update_agent` 能被返回，API 也未调用 `start_watching()` 方法

---

### 原因 3：upload_dir 使用相对路径

**配置位置**: `config/settings.py`

```python
upload_dir: str = "./uploads"  # 相对路径
```

**代码位置**: `api/main.py`

```python
# 原代码
update_agent.start_watching(settings.upload_dir)  # 传入相对路径
```

**问题**: Watchdog 收到相对路径 `./uploads`，无法正确监控

---

### 原因 4：Watchdog 异常被静默吞噬

**代码位置**: `knowledge_update_agent.py`

```python
# 原代码
def on_created(self, event):
    if not event.is_directory:
        asyncio.run(agent.process_change(change))  # 无异常处理
```

**问题**: `asyncio.run()` 抛出的异常无处可见，调试困难

---

## 三、修复方法

### 修复 1：返回 update_agent 实例

**文件**: `orchestrator/graph.py`

```python
# 修改前
def build_knowledge_graph_workflow(...) -> dict[str, Any]:
    return {...}

# 修改后
def build_knowledge_graph_workflow(...) -> tuple[dict[str, Any], KnowledgeUpdateAgent]:
    ...
    return workflows, update_agent
```

---

### 修复 2：启动时调用 start_watching()

**文件**: `api/main.py`

```python
# 添加全局变量
update_agent: "KnowledgeUpdateAgent | None" = None

# 修改启动逻辑
global update_agent
workflows_dict, update_agent = build_knowledge_graph_workflow(...)
workflows.update(workflows_dict)

# 启动 Watchdog（使用绝对路径）
upload_dir_abs = os.path.abspath(settings.upload_dir)
update_agent.start_watching(upload_dir_abs)
```

---

### 修复 3：使用绝对路径

```python
upload_dir_abs = os.path.abspath(settings.upload_dir)
# ./uploads → /app/uploads
```

---

### 修复 4：添加错误处理和调试日志

**文件**: `knowledge_update_agent.py`

```python
class _Handler(FileSystemEventHandler):
    def __init__(self):
        super().__init__()
        print(f"[Watchdog] _Handler initialized, watching: {directory}", flush=True)

    def on_created(self, event):
        if not event.is_directory:
            import asyncio
            print(f"[Watchdog] on_created: {event.src_path}", flush=True)
            try:
                change = DocumentChange(file_path=event.src_path, change_type=ChangeType.CREATED)
                asyncio.run(agent.process_change(change))
            except Exception as e:
                print(f"[Watchdog] ERROR in on_created: {e}", flush=True)
```

---

## 四、验证结果

### 日志输出

```
[DEBUG] upload_dir_abs = /app/uploads
[DEBUG] update_agent = <agents.knowledge_update_agent.KnowledgeUpdateAgent object at ...>
[DEBUG] Calling start_watching...
[Watchdog] _Handler initialized, watching: /app/uploads
[DEBUG] start_watching called successfully
[Watchdog] on_created: /app/uploads/new_test.txt
```

### 实际测试

1. 在 `python/uploads/` 创建文件 `new_test.txt`，内容：`赵雷是腾讯的产品经理`
2. Watchdog 检测到文件变化
3. 自动调用 `process_change()` 进行增量更新
4. Neo4j 中成功创建实体：赵雷、腾讯

---

## 五、最终配置

### docker-compose.yml（关键部分）

```yaml
services:
  api:
    volumes:
      - ./python:/app
      - ./python/uploads:/app/uploads  # 绑定挂载，确保文件同步
```

### config/settings.py

```python
upload_dir: str = "./uploads"  # 相对路径，启动时转换为绝对路径
```

---

## 六、总结

| 问题 | 根因 | 解决方案 |
|-----|------|---------|
| update_agent 无法访问 | 函数只返回 dict | 修改返回类型为 tuple |
| 未调用 start_watching | API 启动时未集成 | 在 lifespan 中调用 |
| Watchdog 路径错误 | 相对路径无法定位 | os.path.abspath() 转换 |
| 异常无法追踪 | 缺少错误处理 | 添加 try-except 和日志 |

---

## 七、后续建议

1. **移除调试日志**: 正式环境可移除 `[DEBUG]` 和 `[Watchdog]` 日志
2. **Watchdog 进程管理**: 可考虑添加优雅关闭机制
3. **配置化监控目录**: 将监控目录移到配置文件中
