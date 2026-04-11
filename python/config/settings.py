"""
应用配置 — 通过环境变量或 .env 文件加载
所有配置项都有默认值，可通过 .env 文件或环境变量覆盖
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ═══════════════════════════════════════════════════════════
    # LLM 配置（大型语言模型）
    # ═══════════════════════════════════════════════════════════

    openai_api_key: str = ""    # DashScope API 密钥（用于调用通义千问等阿里云LLM服务）

    # LLM API 地址（DashScope兼容OpenAI格式的端点）
    # 国内用阿里云: https://dashscope.aliyuncs.com/compatible-mode/v1
    # 官方OpenAI: https://api.openai.com/v1
    openai_base_url: str = "https://api.openai.com/v1"

    # LLM 模型名称（DashScope常用: qwen3.5-flash / qwen-plus）
    openai_model: str = "gpt-4o"

    # Embedding 模型名称（DashScope: text_embedding_v3）
    # ⚠️ 注意：DashScope格式是下划线，OpenAI格式是破折号
    embedding_model: str = "text_embedding_v3"

    # ═══════════════════════════════════════════════════════════
    # Neo4j 知识图谱配置。  蜘蛛网 存关系型数据库
    # ═══════════════════════════════════════════════════════════

    neo4j_uri: str = "bolt://localhost:7687"    # Neo4j 数据库连接地址（bolt是Neo4j的专用协议）

    neo4j_user: str = "neo4j"   # Neo4j 用户名

    neo4j_password: str = "password"   # Neo4j 密码 

    # ═══════════════════════════════════════════════════════════
    # 向量数据库配置（存储文档嵌入向量） 按照语义相似度找内容
    # ═══════════════════════════════════════════════════════════
    #开发用chroma，生产用pgvector（PostgreSQL扩展）。ChromaDB轻量易用，适合开发和小规模应用；PGVector性能更好，适合大规模生产环境。
    vector_store_type: str = "chroma"    # 向量库类型 chroma（轻量级）或 pgvector（PostgreSQL扩展）

    chroma_host: str = "localhost"   # ChromaDB 服务地址（Docker启动时映射到 localhost:8000）

    chroma_port: int = 8000   # ChromaDB 端口
    
    # PGVector 连接字符串（当 vector_store_type=pgvector 时使用）
    # 格式: postgresql://用户名:密码@主机:端口/数据库名
    pgvector_dsn: str = "postgresql://postgres:postgres@localhost:5432/knowledge"

    # ═══════════════════════════════════════════════════════════
    # Kafka 配置（CDC增量更新消息队列）。文档更新时通知各方
    # ═══════════════════════════════════════════════════════════

    kafka_bootstrap_servers: str = "localhost:9092"   # Kafka broker 地址（Docker启动时映射到 localhost:9092）

    kafka_topic_doc_changes: str = "doc-changes" # Kafka Topic：文档变更事件（当文档被上传/修改/删除时产生）

    kafka_topic_kg_updates: str = "kg-updates"  # Kafka Topic：知识图谱更新事件（当实体/关系发生变化时产生）

    # ═══════════════════════════════════════════════════════════
    # API 服务配置
    # ═══════════════════════════════════════════════════════════

    # API 服务监听地址（0.0.0.0 表示接受所有网络访问）
    api_host: str = "0.0.0.0"

    # API 服务端口（Docker映射到 localhost:8080）
    api_port: int = 8080

    # ═══════════════════════════════════════════════════════════
    # 文件存储配置
    # ═══════════════════════════════════════════════════════════

    # 用户上传文件的存储目录（相对路径，相对于 python/ 目录）
    upload_dir: str = "./uploads"

    # pydantic-settings 会在项目根目录查找 .env 文件
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
