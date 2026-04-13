"""
知识更新 Agent — 监听文档变更，增量更新向量库和知识图谱

核心能力:
  1. 文件系统监听 (Watchdog) / Kafka CDC 消费
  2. 差量对比：对比新旧文档，只处理变更部分
  3. 增量向量化 & 图谱更新
  4. 版本管理：知识节点带时间戳和版本号
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from config import settings


class ChangeType(str, Enum):
    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"


@dataclass
class DocumentChange:
    file_path: str
    change_type: ChangeType
    timestamp: float = field(default_factory=time.time)
    old_hash: str = ""
    new_hash: str = ""
    diff_chunks: list[str] = field(default_factory=list)


@dataclass
class UpdateResult:
    change: DocumentChange
    vectors_added: int = 0
    vectors_deleted: int = 0
    entities_added: int = 0
    entities_updated: int = 0
    relations_added: int = 0
    success: bool = True
    error: str = ""
    processing_time_ms: float = 0


class KnowledgeUpdateAgent:
    """
    知识更新 Agent

    支持两种模式:
      1. 文件监听模式 (Watchdog): 监听本地文件系统变更
      2. CDC 模式 (Kafka): 消费来自消息队列的变更事件

    工作流:
      detect_change → diff_analysis → incremental_parse → update_vector_store → update_knowledge_graph → log
    """

    def __init__(
        self,
        doc_parser: Any = None,
        knowledge_extractor: Any = None,
        vector_store: Any = None,
        knowledge_graph: Any = None,
    ) -> None:
        self.doc_parser = doc_parser
        self.knowledge_extractor = knowledge_extractor
        self.vector_store = vector_store
        self.knowledge_graph = knowledge_graph
        self._file_hashes: dict[str, str] = {}
        self._version_counter: dict[str, int] = {}

    # ── public API ───────────────────────────────────────────

    async def process_change(self, change: DocumentChange) -> UpdateResult:
        """处理单个文档变更"""
        start = time.time()
        result = UpdateResult(change=change)

        try:
            if change.change_type == ChangeType.DELETED:
                await self._handle_delete(change, result)
            elif change.change_type == ChangeType.CREATED:
                await self._handle_create(change, result)
            elif change.change_type == ChangeType.MODIFIED:
                await self._handle_modify(change, result)
        except Exception as e:
            result.success = False
            result.error = str(e)

        result.processing_time_ms = (time.time() - start) * 1000
        return result

    async def process_batch(self, changes: list[DocumentChange]) -> list[UpdateResult]:
        """批量处理文档变更"""
        results: list[UpdateResult] = []
        for change in changes:
            results.append(await self.process_change(change))
        return results

    def detect_changes(self, file_paths: list[str]) -> list[DocumentChange]:
        """扫描文件列表，检测变更"""
        changes: list[DocumentChange] = []
        current_files = set(file_paths)

        for fp in current_files:
            new_hash = self._compute_hash(fp)
            old_hash = self._file_hashes.get(fp, "")

            if not old_hash:
                changes.append(DocumentChange(
                    file_path=fp,
                    change_type=ChangeType.CREATED,
                    new_hash=new_hash,
                ))
            elif new_hash != old_hash:
                changes.append(DocumentChange(
                    file_path=fp,
                    change_type=ChangeType.MODIFIED,
                    old_hash=old_hash,
                    new_hash=new_hash,
                ))
            self._file_hashes[fp] = new_hash

        for fp in set(self._file_hashes) - current_files:
            changes.append(DocumentChange(
                file_path=fp,
                change_type=ChangeType.DELETED,
                old_hash=self._file_hashes[fp],
            ))
            del self._file_hashes[fp]

        return changes

    # ── watchdog mode ────────────────────────────────────────

    def start_watching(self, directory: str, main_loop: Any = None) -> None:
        """启动文件系统监听（非阻塞，在独立线程运行）

        Args:
            directory: 要监控的目录路径
            main_loop: FastAPI 主事件循环，用于在线程中调度 async 任务
        """
        import threading
        import asyncio
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers.polling import PollingObserver as Observer

        agent = self
        import sys

        class _Handler(FileSystemEventHandler):
            def __init__(self):
                super().__init__()
                print(f"[Watchdog] _Handler initialized, watching: {directory}", flush=True)
                sys.stdout.flush()

            @staticmethod
            def _wait_until_file_stable(file_path: str, timeout_s: float = 1.0, interval_s: float = 0.1) -> None:
                """等待文件写入稳定，避免在编辑器尚未写完时读取到半成品内容。"""
                start = time.time()
                prev_sig: tuple[float, int] | None = None
                while time.time() - start < timeout_s:
                    try:
                        stat = os.stat(file_path)
                        sig = (stat.st_mtime, stat.st_size)
                        if sig == prev_sig:
                            return
                        prev_sig = sig
                    except FileNotFoundError:
                        pass
                    time.sleep(interval_s)

            def _process(self, file_path: str, change_type: ChangeType) -> None:
                """处理文件变更事件，在主事件循环中执行 async 任务"""
                import asyncio
                import threading

                normalized_path = os.path.abspath(file_path)
                old_hash = ""
                new_hash = ""

                if change_type in (ChangeType.CREATED, ChangeType.MODIFIED):
                    self._wait_until_file_stable(normalized_path)
                    old_hash = agent._file_hashes.get(normalized_path, "")
                    new_hash = agent._compute_hash(normalized_path)
                    if change_type == ChangeType.MODIFIED and old_hash and new_hash == old_hash:
                        print(f"[Watchdog] skip unchanged file: {normalized_path}", flush=True)
                        return
                elif change_type == ChangeType.DELETED:
                    old_hash = agent._file_hashes.get(normalized_path, "")

                change = DocumentChange(
                    file_path=normalized_path,
                    change_type=change_type,
                    old_hash=old_hash,
                    new_hash=new_hash,
                )

                result = None
                if main_loop is not None:
                    # 通过 HTTP 请求让 API 主线程处理文件变更
                    # 这避免了跨线程事件循环冲突问题
                    import urllib.request
                    import json

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
                        with urllib.request.urlopen(req, timeout=1800) as response:
                            resp_data = json.loads(response.read().decode('utf-8'))
                            print(f"[Watchdog] HTTP update result: {resp_data.get('success', False)}", flush=True)
                            # 构造一个假的 result 对象来保持日志输出
                            class FakeResult:
                                success = resp_data.get('success', False)
                                vectors_added = resp_data.get('vectors_added', 0)
                                vectors_deleted = resp_data.get('vectors_deleted', 0)
                                entities_added = resp_data.get('entities_added', 0)
                                entities_updated = resp_data.get('entities_updated', 0)
                                relations_added = resp_data.get('relations_added', 0)
                                processing_time_ms = resp_data.get('processing_time_ms', 0)
                                error = resp_data.get('error', '') or None
                            result = FakeResult()
                    except Exception as e:
                        print(f"[Watchdog] HTTP update failed: {e}", flush=True)
                else:
                    # 降级方案：直接使用 asyncio.run()
                    result = asyncio.run(agent.process_change(change))

                if result and result.success:
                    if change_type in (ChangeType.CREATED, ChangeType.MODIFIED) and new_hash:
                        agent._file_hashes[normalized_path] = new_hash
                    elif change_type == ChangeType.DELETED:
                        agent._file_hashes.pop(normalized_path, None)

                if result:
                    print(
                        "[Watchdog] processed "
                        f"{change_type.value}: success={result.success}, "
                        f"vectors(+{result.vectors_added}/-{result.vectors_deleted}), "
                        f"entities(+{result.entities_added}/~{result.entities_updated}), "
                        f"relations(+{result.relations_added}), "
                        f"time={result.processing_time_ms:.1f}ms, "
                        f"error={result.error or 'None'}",
                        flush=True,
                    )

            def on_created(self, event):
                if not event.is_directory:
                    print(f"[Watchdog] on_created: {event.src_path}", flush=True)
                    sys.stdout.flush()
                    try:
                        self._process(event.src_path, ChangeType.CREATED)
                    except Exception as e:
                        print(f"[Watchdog] ERROR in on_created: {e}", flush=True)
                        sys.stdout.flush()

            def on_modified(self, event):
                if not event.is_directory:
                    print(f"[Watchdog] on_modified: {event.src_path}", flush=True)
                    sys.stdout.flush()
                    try:
                        self._process(event.src_path, ChangeType.MODIFIED)
                    except Exception as e:
                        print(f"[Watchdog] ERROR in on_modified: {e}", flush=True)
                        sys.stdout.flush()

            def on_deleted(self, event):
                if not event.is_directory:
                    print(f"[Watchdog] on_deleted: {event.src_path}", flush=True)
                    sys.stdout.flush()
                    try:
                        self._process(event.src_path, ChangeType.DELETED)
                    except Exception as e:
                        print(f"[Watchdog] ERROR in on_deleted: {e}", flush=True)
                        sys.stdout.flush()

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

    # ── kafka CDC mode ───────────────────────────────────────

    async def start_kafka_consumer(self) -> None:
        """启动 Kafka CDC 消费者"""
        import json
        from confluent_kafka import Consumer

        conf = {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "group.id": "knowledge-update-agent",
            "auto.offset.reset": "latest",
        }
        consumer = Consumer(conf)
        consumer.subscribe([settings.kafka_topic_doc_changes])

        try:
            while True:
                msg = consumer.poll(timeout=1.0)
                if msg is None:
                    continue
                if msg.error():
                    continue
                payload = json.loads(msg.value().decode("utf-8"))
                change = DocumentChange(
                    file_path=payload["file_path"],
                    change_type=ChangeType(payload["change_type"]),
                    old_hash=payload.get("old_hash", ""),
                    new_hash=payload.get("new_hash", ""),
                )
                await self.process_change(change)
        finally:
            consumer.close()

    # ── internal handlers ────────────────────────────────────

    async def _handle_create(self, change: DocumentChange, result: UpdateResult) -> None:
        if not self.doc_parser:
            return
        source_path = os.path.abspath(change.file_path)
        chunks = await self.doc_parser.parse(source_path)

        if self.vector_store:
            await self.vector_store.add_chunks(chunks)
            result.vectors_added = len(chunks)

        if self.knowledge_extractor and self.knowledge_graph:
            extractions = await self.knowledge_extractor.extract(chunks)
            for ext in extractions:
                for ent in ext.entities:
                    version = self._bump_version(ent.name)
                    await self.knowledge_graph.upsert_entity(ent, version=version, source=source_path)
                    result.entities_added += 1
                for rel in ext.relations:
                    await self.knowledge_graph.add_relation(rel, source=source_path)
                    result.relations_added += 1

    async def _handle_modify(self, change: DocumentChange, result: UpdateResult) -> None:
        source_path = os.path.abspath(change.file_path)
        doc_id = hashlib.sha256(source_path.encode()).hexdigest()[:16]

        if self.vector_store:
            deleted = await self.vector_store.delete_by_doc_id(doc_id)
            result.vectors_deleted = deleted

        # 保持与向量库一致：modified 时先清理旧图谱，再按新内容重建
        if self.knowledge_graph:
            await self.knowledge_graph.delete_by_source(source_path)

        await self._handle_create(change, result)

    async def _handle_delete(self, change: DocumentChange, result: UpdateResult) -> None:
        source_path = os.path.abspath(change.file_path)
        doc_id = hashlib.sha256(source_path.encode()).hexdigest()[:16]

        if self.vector_store:
            deleted = await self.vector_store.delete_by_doc_id(doc_id)
            result.vectors_deleted = deleted

        if self.knowledge_graph:
            await self.knowledge_graph.delete_by_source(source_path)

    # ── utilities ────────────────────────────────────────────

    @staticmethod
    def _compute_hash(file_path: str) -> str:
        try:
            with open(file_path, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()
        except FileNotFoundError:
            return ""

    def _bump_version(self, entity_name: str) -> int:
        ver = self._version_counter.get(entity_name, 0) + 1
        self._version_counter[entity_name] = ver
        return ver
