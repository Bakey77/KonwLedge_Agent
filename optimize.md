1.调用LLM多模态能力描述图片内容的时候，原来使用的openai，现在使用的qwen，是否需要修改。qa_agent好像也调用gpt接口了。 ✅
2.async def extract方法中，去重（保证实体的唯一性）逻辑合理性检查
3.`delete_by_doc_id()` 为什么对增量更新有用？怎么在面试的时候讲清楚
4.先测试当切分块的召回和答案准确率，如何“按语义边界优先 + token 长度兜底”的分块策略，并用召回率/答案准确率做离线评估再定参数。 （excel是20行一块）
5.PDF解析中，对于页眉页脚是怎么处理的？✅
6.我在设计意图分类提示词的时候遇到了什么问题？
7.为什么 QA 在图层只有一个节点，但内部仍是多阶段流程
8.：代码里 entities_updated 字段从未被赋值，一直为 0。所有 upsert 都计入了 entities_added。这是一个设计上的不完整之处——实际上应该区分「首次创建」和「已有更新」。
9.批量处理多个文档变更，简单的顺序处理，未使用并行。如需提升吞吐量，可以改为 asyncio.gather() 并行处理。

11.总结：Watchdog 自动监控流程
  1. API 启动 → lifespan() → start_watching("/app/uploads")
  2. 用户在 python/uploads/ 创建/修改/删除文件
  3. Watchdog 检测到文件变化，自动触发 on_created/on_modified/on_deleted
  4. asyncio.run(agent.process_change(change)) 执行增量更新
  5. 实体自动入库到 Neo4j
RAG搭建完成之后的评估：
1.检索层：命中率、排名
2.生成层：忠实度、相关性。 忠实度是最重要的，来检测ai有没有说文档里没有的（幻觉）
日志里出现了 Future attached to a different loop，这通常是异步对象在不同线程/事件循环混用。下一步我会带时间戳抽取 on_deleted、/api/admin/stats 和报错三类日志，确认它们的先后关系。


1.把modified也修改为清理再重建 ✅
2.增量更新。并且看实体数和关系数 已经时间 ✅
当你执行增量更新时：

  1. 你在本地添加 "小黑开发了美团"
  2. curl 发送增量更新请求到 /api/admin/update
  3. API 读取 /app/uploads/text.txt（容器内路径）
  4. 容器内文件还是188字节的旧内容（没有小黑和美团）
  5. LLM 解析旧内容 → 提取出原来的7个实体和6个关系
  6. 增量更新流程：
     - delete_by_source (删除旧实体) → 删除了7个实体
     - _handle_create (重新创建) → 重新创建了那7个旧实体
     - 因为文件内容没变，所以结果和之前一样！

  不是 LLM 的问题，也不是代码的问题，而是文件根本没有同步到容器里！
  ---
  解决方案

  修改 docker-compose.yml，把命名卷改为绑定挂载：

  volumes:
    - ./python:/app   #初始化时容器镜像的内容会复制到卷里面，之后不相通
    - ./python/uploads:/app/uploads:ro   # 绑定挂载到本地目录， 同一个目录，修改立刻双向同步

  或者在修改本地文件后，手动同步到容器：
  docker cp python/uploads/text.txt agenthub-api:/app/uploads/text.txt


1.调用LLM多模态能力描述图片内容的时候，原来使用的openai，现在使用的qwen，是否需要修改。qa_agent好像也调用gpt接口了。 ✅
2.async def extract方法中，去重（保证实体的唯一性）逻辑合理性检查
3.`delete_by_doc_id()` 为什么对增量更新有用？怎么在面试的时候讲清楚
4.先测试当切分块的召回和答案准确率，如何“按语义边界优先 + token 长度兜底”的分块策略，并用召回率/答案准确率做离线评估再定参数。 （excel是20行一块）
5.PDF解析中，对于页眉页脚是怎么处理的？✅
6.我在设计意图分类提示词的时候遇到了什么问题？
7.为什么 QA 在图层只有一个节点，但内部仍是多阶段流程
8.：代码里 entities_updated 字段从未被赋值，一直为 0。所有 upsert 都计入了 entities_added。这是一个设计上的不完整之处——实际上应该区分「首次创建」和「已有更新」。
9.批量处理多个文档变更，简单的顺序处理，未使用并行。如需提升吞吐量，可以改为 asyncio.gather() 并行处理。





















































第 3 天
目标：理解 API 和配置
做什么：

读 settings.py
读 main.py
搞懂每个接口做什么


第 4 天
目标：理解工作流
做什么：

读 graph.py
画出 ingest / qa / update 三条流程图
第 5 天
目标：理解文档解析和知识抽取
做什么：

读 doc_parser_agent.py
读 knowledge_extract_agent.py

第 6 天
目标：理解问答
做什么：

读 qa_agent.py
读 vector_store.py
读 knowledge_graph.py
第 7 天
目标：理解增量更新和扩展
做什么：

读 knowledge_update_agent.py
读 cdc_processor.py
想一想如果你来改，会先改哪里