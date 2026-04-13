# Watchdog asyncio.run() 与 FastAPI 事件循环冲突问题修复日志

**日期**: 2026-04-12
**问题**: Watchdog 中 `asyncio.run()` 与 FastAPI 主事件循环冲突，导致 `on_modified` 时实体无法正确更新
**状态**: 已修复

---

## 一、问题现象

### 观察到的现象

1. **添加文件时** — Watchdog 可以正常完成增量更新 ✅
2. **删除文件时** — 如果不刷新 Neo4j 界面，等待一段时间后删除也能被执行 ✅
3. **删除文件时（刷新 Neo4j 界面后）** — 删除被中断，文件删除但实体数保持不变 ❌
4. **修改文件时** — 实体不更新，旧实体保留 ❌

### 日志中的错误信息

```
RuntimeError: Task <Task pending name='Task-34' coro=<RequestResponseCycle.run_asgi()>>
  got Future <Future pending> attached to a different loop
```

---

## 二、问题根因分析

### 代码架构

```
┌─────────────────────────────────────────────────────────────┐
│  FastAPI 主线程                                             │
│  ├── 主事件循环 (asyncio event loop)                        │
│  ├── Neo4j AsyncGraphDatabase.driver (绑定到主事件循环)    │
│  └── API 请求处理                                           │
└─────────────────────────────────────────────────────────────┘
                            ▲
                            │ start_watching()
┌─────────────────────────────────────────────────────────────┐
│  Watchdog 独立线程                                         │
│  ├── Observer (文件系统监控)                               │
│  └── _Handler (事件处理)                                  │
│      └── asyncio.run(agent.process_change(change))  ← 问题  │
└─────────────────────────────────────────────────────────────┘
```

### 根因

`asyncio.run()` 在 Watchdog 独立线程中调用时：
1. 创建**新的事件循环**
2. 但 Neo4j `AsyncGraphDatabase.driver` 绑定到 **FastAPI 主线程的事件循环**
3. 两个事件循环不兼容，导致 Neo4j 驱动无法正常工作

### 受影响的操作

| 操作 | 代码路径 | 是否受影响 |
|-----|---------|-----------|
| on_created | `_handle_create()` → `doc_parser.parse()` + `knowledge_graph.upsert_entity()` | ❌ 有时成功 |
| on_modified | `delete_by_source()` + `_handle_create()` | ✅ 失败 |
| on_deleted | `delete_by_source()` | ✅ 失败 |

---

## 三、修复尝试过程

### 尝试 1：直接传递主事件循环

**思路**: 在 `start_watching()` 时传入主事件循环，直接调用 `main_loop.run_until_complete()`

```python
# knowledge_update_agent.py
def start_watching(self, directory: str, main_loop: Any = None) -> None:
    ...
    if main_loop is not None:
        future = asyncio.ensure_future(process_with_loop())
        main_loop.run_until_complete(future)  # ❌ 失败
```

**结果**: 错误 `There is no current event loop in thread 'Thread-1'`

**原因**: `run_until_complete()` 不能从其他线程调用

---

### 尝试 2：使用 threading.Event 等待

**思路**: 在主事件循环中调度任务，用 Event 同步

```python
if main_loop is not None:
    event = threading.Event()
    async def process_and_notify():
        result_holder[0] = await agent.process_change(change)
        main_loop.call_soon_threadsafe(event.set)

    future = asyncio.ensure_future(process_and_notify())
    main_loop.run_until_complete(future)  # ❌ 同样失败
    event.wait()
```

**结果**: 错误 `There is no current event loop in thread 'Thread-1'`

**原因**: `asyncio.ensure_future()` 在没有事件循环的线程中无法工作

---

### 尝试 3：使用 create_task 替代 ensure_future

**思路**: 在主事件循环中创建任务

```python
if main_loop is not None:
    task = main_loop.create_task(process_and_notify())  # ❌ 失败
    event.wait()
```

**结果**: 任务没有被执行，Watchdog 线程阻塞在 `event.wait()`

**原因**: `create_task()` 返回的任务从未被调度执行

---

### 尝试 4（最终成功）：HTTP 请求代替直接调用

**思路**: Watchdog 线程通过 HTTP 请求调用 API 端点，让 API 主线程处理文件变更

```python
if main_loop is not None:
    import urllib.request
    import json

    payload = json.dumps({
        "file_path": change.file_path,
        "change_type": change.change_type.value
    }).encode('utf-8')

    req = urllib.request.Request(
        "http://localhost:8080/api/admin/update",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        resp_data = json.loads(response.read().decode('utf-8'))
```

**结果**: ✅ 成功！

**原因**: HTTP 请求在独立的网络线程中执行，不与主事件循环冲突。API 端点在主事件循环中处理请求，Neo4j 驱动正常工作。

---

## 四、修复代码

### 修改 1：knowledge_update_agent.py

```python
# ── watchdog mode ────────────────────────────────────────

def start_watching(self, directory: str, main_loop: Any = None) -> None:
    """启动文件系统监听（非阻塞，在独立线程运行）"""
    import threading
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    agent = self
    import sys

    class _Handler(FileSystemEventHandler):
        def __init__(self):
            super().__init__()
            print(f"[Watchdog] _Handler initialized, watching: {directory}", flush=True)

        def _process(self, file_path: str, change_type: ChangeType) -> None:
            """处理文件变更事件，通过 HTTP 请求触发 API 处理"""
            import urllib.request
            import json

            normalized_path = os.path.abspath(file_path)
            # ... hash 计算逻辑省略 ...

            change = DocumentChange(...)

            # 通过 HTTP 请求让 API 主线程处理
            try:
                payload = json.dumps({
                    "file_path": change.file_path,
                    "change_type": change.change_type.value
                }).encode('utf-8')

                req = urllib.request.Request(
                    "http://localhost:8080/api/admin/update",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )

                with urllib.request.urlopen(req, timeout=30) as response:
                    resp_data = json.loads(response.read().decode('utf-8'))
                    print(f"[Watchdog] HTTP update result: {resp_data.get('success', False)}", flush=True)

            except Exception as e:
                print(f"[Watchdog] ERROR in HTTP update: {e}", flush=True)

        def on_created(self, event):
            if not event.is_directory:
                print(f"[Watchdog] on_created: {event.src_path}", flush=True)
                try:
                    self._process(event.src_path, ChangeType.CREATED)
                except Exception as e:
                    print(f"[Watchdog] ERROR in on_created: {e}", flush=True)

        def on_modified(self, event):
            if not event.is_directory:
                print(f"[Watchdog] on_modified: {event.src_path}", flush=True)
                try:
                    self._process(event.src_path, ChangeType.MODIFIED)
                except Exception as e:
                    print(f"[Watchdog] ERROR in on_modified: {e}", flush=True)

        def on_deleted(self, event):
            if not event.is_directory:
                print(f"[Watchdog] on_deleted: {event.src_path}", flush=True)
                try:
                    self._process(event.src_path, ChangeType.DELETED)
                except Exception as e:
                    print(f"[Watchdog] ERROR in on_deleted: {e}", flush=True)

    observer = Observer()
    observer.schedule(_Handler(), directory, recursive=True)

    def _run():
        observer.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            observer.stop()
        observer.join()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
```

### 修改 2：api/main.py

```python
import asyncio  # 添加 asyncio 导入

# 添加全局变量
_main_event_loop: asyncio.AbstractEventLoop | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... 初始化代码省略 ...

    # 构建工作流后，保存主事件循环引用
    global _main_event_loop
    _main_event_loop = asyncio.get_running_loop()

    # 启动 Watchdog（传入主事件循环，虽然当前方案不需要用到）
    upload_dir_abs = os.path.abspath(settings.upload_dir)
    update_agent.start_watching(upload_dir_abs, _main_event_loop)

    yield

    await knowledge_graph.close()
```

---

## 五、验证结果

### 测试用例

```bash
# 1. 创建文件
echo "李明在字节跳动工作" > python/uploads/test_new.txt

# 2. 修改文件
echo "王强在美团工作" > python/uploads/test_new.txt

# 3. 删除文件
rm python/uploads/test_new.txt
```

### 日志输出

```
# 创建
[Watchdog] on_created: /app/uploads/test_new.txt
[Watchdog] HTTP update result: True
[Watchdog] processed created: vectors(+1), entities(+2), relations(+1), time=20820.0ms

# 修改
[Watchdog] on_modified: /app/uploads/test_new.txt
[Watchdog] HTTP update result: True
[Watchdog] processed modified: vectors(+1/-1), entities(+2/~0), relations(+1), time=16701.3ms

# 删除
[Watchdog] on_deleted: /app/uploads/test_new.txt
[Watchdog] HTTP update result: True
[Watchdog] processed deleted: vectors(+0/-1), entities(+0/~0), relations(+0), time=256.4ms
```

### Neo4j 验证

| 操作 | 实体变化 | 验证 |
|-----|---------|------|
| 创建后 | 李明、字节跳动 (2个) | ✅ |
| 修改后 | 王强、美团 (2个) | ✅ |
| 删除后 | 0个 | ✅ |

---

## 六、总结

| 尝试 | 方案 | 结果 |
|-----|------|------|
| 1 | `main_loop.run_until_complete()` | ❌ 跨线程调用失败 |
| 2 | `asyncio.ensure_future()` + Event | ❌ 事件循环不存在 |
| 3 | `main_loop.create_task()` | ❌ 任务未执行 |
| 4 | HTTP 请求 | ✅ 成功 |

### 最终方案

通过 **HTTP 请求** 触发 API 端点处理文件变更：
- Watchdog 线程不直接调用 async 方法
- 通过 `urllib.request` 发送 HTTP POST 请求到 `/api/admin/update`
- API 主线程在正常的事件循环中处理请求
- 避免了一切跨事件循环的 async 操作

### 优点
1. 简单直接，不需要复杂的线程同步
2. 复用 API 的已有端点，代码重复少
3. 可观测性好，HTTP 请求有日志记录

### 缺点
1. 额外的网络开销（但文件操作本身是 IO）
2. 需要 API 端口可用（localhost:8080）
