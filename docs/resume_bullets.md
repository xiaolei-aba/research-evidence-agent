# ResearchEvidence Agent：按岗位选择简历要点

项目：面向AI研发知识与科研文献的多步检索及证据核验智能体。英文名：ResearchEvidence Agent — Evidence-Grounded Research Agent for AI Technical Knowledge。

这是Codex辅助实现、测试、实验执行及文档审计的项目。以下描述对应项目真实产物，不自动代表全部工作由本人独立完成；投递前按[开发归属说明](development_attribution.md)确认本人负责的范围。使用“在Codex辅助下完成”这一限定，不能删掉后改称独立开发。

## Agent算法实习

中文可选要点：

- 在Codex辅助下完成本地BM25、JSON Planner、白名单search/read、来源可追溯Memory及有界Critic/Replan工作流；同一Qwen2.5-1.5B Q4_K_M承担规划、回答与Critic，不把模型自评充当独立评估器。
- 完成228条基线Dev、152条证据去重消融、30条效率观察及624条冻结Final，共1034条真实观察；建立终态及artifact SHA校验和断点恢复，失败保留、不重复采样直到答对。
- 按完整Dev预登记规则评估源身份去重：Final QASPER Answer F1由off的0.0747到on的0.1345（108题），仍低于B1的0.1672；定位输入拒绝向总预留/输出截断转移，保留算法退化与成本上升结果。

English options:

- Built, with Codex assistance, a local BM25 agent with a JSON planner, allow-listed search/read tools, provenance-aware evidence memory, and bounded critic/replanning; used the same Qwen2.5-1.5B Q4_K_M for planning, answering, and criticism, without treating self-review as independent evaluation.
- Completed 1,034 real observations across baseline Dev, deduplication ablations, efficiency measurements, and frozen Final evaluation; preserved terminal failures and used SHA-verified recovery without resampling until success.
- Evaluated preregistered source-identity deduplication on 108 held-out QASPER questions: Answer F1 was 0.1345 versus 0.0747 with the component disabled, while remaining below the direct B1 baseline of 0.1672; documented budget-failure transfer and increased costs.

## RAG / 搜索算法实习

中文可选要点：

- 在Codex辅助下实现指定论文段落检索与全语料句子BM25检索，保存document/source/version/chunk、原文span及SHA；以来源结构核验约束引用，明确语义支持未经过独立人审。
- 完成单因素Memory去重对照，保留相同正文的不同来源/版本与首次Evidence ID；Final QASPER段落Evidence F1为0.1221，off为0.0871，B1为0.1345，按任务失败进入固定分母评估。
- 分开报告检索Recall、答案及证据质量；100条SciFact Final上B4 Label Accuracy为0.0200、sentence-label micro F1为0.0141，均低于B1，未宣称训练embedding/reranker或全面超过基线。

English options:

- Implemented, with Codex assistance, paper-scoped paragraph retrieval and corpus-wide sentence BM25 retrieval, retaining source versions, text spans, and SHA hashes; separated structural citation validation from unreviewed semantic support.
- Ran a single-factor evidence-memory deduplication ablation that retained distinct sources/versions and original evidence identities; Final QASPER Evidence F1 was 0.1221 versus 0.0871 with deduplication disabled and 0.1345 for B1, with failures included in the fixed denominator.
- Reported retrieval recall, answer quality, and evidence quality separately; B4 reached 0.0200 label accuracy and 0.0141 sentence-label micro F1 on 100 SciFact claims, below B1, without claiming a trained embedding model or reranker.

## LLM应用算法实习

中文可选要点：

- 在Codex辅助下构建可配置OpenAI-compatible本地Provider、JSON输出校验、原生Tokenizer计数与usage对账、有限transport重试及120秒任务deadline，API密钥仅从环境变量读取。
- 实现RAM/GPU守卫、parent/worker/backend Private Bytes与Working Set采样及来源SHA阻断；区分系统Available/Committed与进程/显存指标，保持1.25 GiB RAM安全阈值，未终止外部应用。
- 通过160次合成工程请求验证`--cache-ram 0`及cache state为0，暖机后backend Private Bytes漂移约1.35 MiB；工程验证与624条真实Final评分分开，不将Mock/Smoke结果当作Agent质量提升。

English options:

- Built, with Codex assistance, a configurable OpenAI-compatible local provider with JSON validation, native token accounting, bounded transport retries, and a 120-second task deadline; read credentials only from environment variables.
- Added RAM/GPU guards, parent/worker/backend Private Bytes and Working Set sampling, and fail-closed SHA checks; distinguished system Available/Committed memory from process and device counters while retaining the 1.25 GiB RAM floor.
- Validated backend cache disabling through a 160-request synthetic engineering soak: cache state remained zero and warm backend Private Bytes drift was approximately 1.35 MiB; kept these engineering checks separate from real Agent evaluation.

## 数字与使用边界

三类要点各取2–3条，避免把全部指标塞进一页简历。F1使用0–1量纲，不改写成“准确率13.45%”；0.0747→0.1345是绝对F1差约+0.0598，不把它写成“准确率提升80%”。SciFact 0.0200表示100条中2条Label正确，必须保留低绝对分数与B1对照。624是108+100个Final任务乘3组，不是624道独立题。1034是观察数，不能改称1034个不同问题。

Final采用单seed11、官方公开数据子集；SciFact项目Final来自官方dev而非隐藏test。QASPER同题108论文簇、SciFact95连接簇，配对bootstrap固定2000次、seed20261009；QASPER B4−off Answer F1区间[0.0246,0.1016]，SciFact Label Accuracy区间[0.0000,0.0510]。这不支持普遍提升、SOTA或稳健多seed结论。B5每benchmark/system仅3题，30条观察仅为工程诊断。

Citation Support Rate及Unsupported Claim Rate均为null，没有独立人工语义标签。去重是复用既有组件的受控算法消融，不能宣称发明BM25、去重、Token最优选择或原生Function Calling。个人已理解/独立编写的模块尚未被记录，不虚构代码归属或本人独立贡献。

指标来源：[公开聚合证据](release_evidence.json)（原始报告保留本地）；原始本地审计JSON SHA256 `4c174d27f7d140dd019bd294065c716368a6bc547ef0f51b8f58fa6add99f5a2`。算法与负结果分别见[b4_algorithm_explained.md](b4_algorithm_explained.md)、本地逐题Dev审计（公开候选不附）。
