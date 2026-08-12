# 抖音知识库

*[English](README.md)*

把收藏的抖音视频变成可搜索的片段。手机上分享一条视频进来，它会变成一组带起止
时间的片段，每条都能跳回视频的那一秒。

要解决的问题很具体：**在抖音上收藏很容易，几个月后想找回来很难。** 这是一个搜索
引擎，不是摘要工具。

```
你: 固定长度切分文档有什么问题

  3:02 – 4:08   语义切片与索引          全文#1 + 向量#1
  按语义单元切分而非固定长度，让每个片段保持完整的语义边界…
  ▶ 跳到 3:02
```

## 它怎么工作

```mermaid
flowchart LR
  A[iOS 快捷指令] -->|分享链接| B[POST /ingest]
  B --> C[(队列)]
  C --> D[解析链接 + 元数据]
  D --> E[音轨 → 带时间戳的转写]
  E --> G[抽取片段]
  G --> H[(Postgres<br/>+ pgvector)]
  H --> I[混合检索]
```

`/ingest` 记录链接后立刻返回；后面的处理要几分钟，跑在 worker 上。处理完媒体
文件全部删除，只留文本。

抽取**只读转写**。关键帧曾经和转写一起送进模型，直到做了对照实验 —— 加上画面
反而让覆盖率下降。数字和这个实验证明不了什么，都写在
[ADR-0009](docs/adr/0009-transcript-only-extraction.md)。

检索同时跑全文和向量两路，按名次融合。只用向量，搜「pgvector」会返回一堆关于
数据库的内容却漏掉真正提到它的那条；只用全文，会漏掉换了说法表达同一件事的片段。

## 跑起来

需要 Docker 和一个 OpenAI API key。

```bash
git clone https://github.com/low122/douyin.git
cd douyin
python3 scripts/setup_env.py     # 生成 .env 和各项密钥
# 把你的 key 填进 .env: OPENAI_API_KEY=sk-...
docker compose up -d --build
docker compose run --rm api alembic upgrade head
```

打开 <http://localhost:8000>，用 `setup_env.py` 生成的 `API_TOKEN` 登录（在
`.env` 里）。

添加视频：在抖音点分享，把复制出来的那坨文本原样发过去，**不用整理**：

```bash
curl -X POST http://localhost:8000/ingest \
  -H "Authorization: Bearer $API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"text": "7.61 YzT:/ ... https://v.douyin.com/XXXXXXX/ 复制此链接..."}'
```

网页上还有一个粘贴框 `/add`，不用开终端：在抖音复制分享文字，粘上，提交。
走的是和上面那个接口完全相同的代码路径。

用 iOS 快捷指令发同样的 POST，就变成从抖音分享面板点两下的事 ——
见 [docs/ios-shortcut.md](docs/ios-shortcut.md)。建好之后它最快，但配置最麻烦；
粘贴框的存在让快捷指令变成一个可选项，而不是前提。

> `.env.example` 增加了新配置项时，**重新跑一次 `python3 scripts/setup_env.py`，
> 不要把它复制覆盖 `.env`**。脚本是合并；复制会抹掉所有生成的密钥和你填的 key。

## 配置

每个 AI 步骤独立解析到自己的 provider 和模型 —— **模型选择是配置，不写在代码里**。
默认四个任务全指向同一家，所以新装只需要一个 key。

| 任务 | 默认 | 为什么 |
|---|---|---|
| `TRANSCRIBE` | `whisper-1` | **唯一返回时间戳的模型**，而片段的定义就是起止时间。更新的那几个更便宜、更快、中文更准，但做不了这件事。 |
| `EXTRACT` | `deepseek-v4-flash` | 抽取只处理文本，不需要多模态模型。在 ADR-0009 的对照里覆盖率与 gpt-4o 相当，输入 token 约五分之一，但慢约四倍。 |
| `EMBED` | `text-embedding-3-small` | 向量列宽在迁移时固定，换模型需要迁移，不只是改环境变量。 |

想按任务分流到不同服务商是可选的 —— 设 `EXTRACT_BASE_URL` 和 `EXTRACT_API_KEY`
就能把某一个任务指向任意 OpenAI 兼容端点。

## 评估改动

```bash
python evals/run_eval.py
```

两层，各管一件事。六项**结构性检查**不需要任何标注，每个视频都跑：抽取覆盖了
多少源视频、时间戳是不是真的、写的是不是用户能搜的语言、相关度打分有没有区分度。
一组**带标注的检索查询**回答另一个问题：搜索能不能找到对的那条。

两层都需要。有一次结构性检查**全绿**，而检索只有 33% Recall@1 —— 格式完美但搜
不到，因为索引里存的是一个 53 秒片段的 60 字摘要，而不是它实际说了什么。

## 它不做什么

- **不做 RAG。** 搜索返回片段，不生成答案。排序错了你看得见 —— 往下翻就行；
  编造的答案你看不见，因为你就是忘了才来搜的。生成要等到检索被证明足够准。
- **不做批量导入。** 一次一条分享链接，这是刻意的。这个"没有"是范围边界，不是
  待办功能。
- **不存媒体文件。** 视频和音频只在一个任务的生命周期内存在。
- **单用户。** 共享令牌认证，没有账号系统。

## 已知局限

- 检索在六条种子查询上是 50% Recall@1。剩下两条没命中的都返回了**相邻片段**而
  不是无关内容 —— 失败模式好一些，但仍然是失败。
- 转写在中文上有同音字错误（大场／大厂）。抽取会用画面和 caption 纠正，**残余
  错误率没有测量过**。
- 抖音对分享页有频率限制。正常使用远达不到，但批量回填几分钟就会撞上。
- **不报告单视频成本。** 每次调用的 token 数都记录了，但价格表只覆盖转写，所以
  抽取和向量化那几行的成本是 NULL —— 宁可留空，不填一个猜的数。

## 文档

- [`docs/design-decisions.md`](docs/design-decisions.md) —— 每个决策、为什么、
  以及否掉了什么
- [`docs/adr/`](docs/adr/) —— 其中难以逆转的那些
- [`docs/douyin-platform-notes.md`](docs/douyin-platform-notes.md) —— 抖音实际
  怎么表现，实测而非查文档
- [`CONTEXT.md`](CONTEXT.md) —— 「视频」和「片段」在这里的确切含义

Python 3.11 · FastAPI · Postgres 16 + pgvector · arq · Docker
