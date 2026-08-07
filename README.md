# DataQuery Agent

DataQuery Agent 是一个本地优先的自然语言数据查询与分析工作台。用户上传 CSV、TSV、XLS、XLSX 或 Parquet 文件后，可以直接用中文提问，系统会自动完成字段检索、SQL 查询、统计分析和可视化，并展示 SQL、证据与执行轨迹。

项目基于 [didilili/shopkeeper-agent](https://github.com/didilili/shopkeeper-agent) 重构，具体来源、基准提交和 MIT 许可证说明见 [NOTICE.md](NOTICE.md)。

## 核心架构

```text
上传文件
  -> DuckDB 建表与数据画像
  -> SQLite 保存工作区和运行记录
  -> TEI 生成字段向量，Qdrant 保存目录索引
  -> 混合检索定位相关表和字段
  -> LangGraph 生成结构化查询/分析计划
  -> sqlglot 校验只读 SQL
  -> DuckDB/Pandas 确定性计算
  -> 生成证据、中文回答和 ECharts 图表
```

RAG 只负责定位表、字段和语义说明，不直接生成数值结论；所有数字均来自 DuckDB 或 Pandas 的执行结果。支持描述统计、分组聚合、相关性、趋势、IQR 异常检测以及受限领域公式分析。

主要技术：FastAPI、LangGraph、DuckDB、SQLite、Pandas、sqlglot、Qdrant、HuggingFace TEI、React、TypeScript、ECharts、Docker Compose。

## Docker 部署

环境要求：Docker Desktop（包含 Docker Compose）。首次启动时，TEI 会将 `BAAI/bge-small-zh-v1.5` 下载到 Docker 数据卷，模型文件不会进入 Git。

```powershell
Copy-Item .env.example .env
# 编辑 .env，填写 LLM_API_KEY
docker compose up --build
```

启动后访问：

- 工作台：http://127.0.0.1:5173
- API：http://127.0.0.1:8000
- 存活检查：http://127.0.0.1:8000/health
- 就绪检查：http://127.0.0.1:8000/ready

Qdrant 和 TEI 也只绑定本机回环地址。创建工作区后上传数据文件即可开始提问；单文件最大 50 MB，单工作区最大 200 MB。表格只展示预览行，完整查询结果支持 CSV 下载，图表支持 PNG 下载。

## 运行边界

这是面向本地单用户场景的工具，不提供公网多租户、外部数据库连接或任意 Python 代码执行。Qdrant/TEI 不可用时会明确显示词法检索降级状态，不会伪装成向量检索成功。
