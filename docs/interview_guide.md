# ResearchEvidence Agent：面试讲解与可核实边界

以下是基于冻结结果的讲解素材。项目由Codex辅助实现、测试、实验与审计；第一人称“我实现/我提出”只能用于本人确认承担并能解释、复现的工作，归属未明处使用“项目实现了”。不要照背后宣称全部独立开发。见[开发归属说明](development_attribution.md)。

## 1分钟项目介绍

“这是一个Codex辅助完成的科研证据Agent项目，面向AI技术知识与文献，用BM25、Planner、证据Memory和最多一次Critic/Replan组织回答。项目完成1034条真实观察，其中624条冻结Final；Gold只用于离线评分，所有失败、Trace和哈希保留。核心消融是完整来源身份去重：QASPER Final Answer F1为0.1345，高于off的0.0747，但低于直接回答B1的0.1672；SciFact B4 Accuracy只有0.0200，也低于B1。这说明局部减少重复上下文可以缓解一个瓶颈，却无法保证多步Agent更准或更省。结构引用验证与未人审的语义支持严格分开。”

## 3分钟技术叙述

先解释任务边界：QASPER在给定论文中找段落并回答；SciFact在完整摘要语料中检索句子并核验科学论断。底层BM25的k1=1.2、b=0.75、K=5，SciFact使用SQLite和有界读取缓存，没有训练embedding/reranker。模型生成经过schema校验的JSON，Python只分派白名单search/read；这是结构化工具协议，不宣称原生Function Calling。

B1直接检索回答，B2加Planner，B3再加跨轮Memory、同模型Critic和一次Replan。完整Dev的B3存储772项证据、181项精确重复，出现42次Token预算失败，命中预登记去重规则。B4复用EvidenceStore的SHA256完整来源身份键，只把重复证据合并并追加任务关联；B4off仅关闭该开关，其他控制相同。

真正困难的是把阶段失败与能力失败分开。每次请求用实际chat template/tokenizer计数并核对usage；输入3072、每次输出512、最坏总预留24000、工具18、模型调用8和120秒deadline各自限流。去重可以使请求更短，也可能让更多Answer/Critic/Replan调用获准，增加真实成本。Dev的QASPER总Token增加31.94%；SciFact的input失败30→4，但total预留失败1→17、Accuracy不变。预算拒绝和length截断仍是正式负结果，不增预算追分。

Final冻结108题QASPER、100条SciFact，B1/B4/off三组624观察，保留323条运行失败。QASPER B4优于off而未超过B1；SciFact B4只有2条Label正确，仍低于B1的10条。配对统计按论文/共享来源连接簇重采样，每次重算micro rationale，2000次、固定seed20261009，不把公开单seed子集解释成普遍提升。

工程恢复维持1.25 GiB RAM门槛，按系统Available/Committed及parent/worker/backend Private Bytes/Working Set分别监控。关闭backend RAM cache经过160次合成工程验证、4类有限配对Smoke；这些不是模型质量证据。历史cache-on/off效率分层，B5每benchmark/system仅3题。整个项目仍没有独立人审语义引用标签，因此Citation Support Rate和Unsupported Claim Rate为null。

## 技术追问卡

| 追问 | 回答依据 |
|---|---|
| B4到底改变了什么？ | 只开启Memory源身份精确去重；不是reranker、全局Token背包或Prompt压缩。详见[b4_algorithm_explained.md](b4_algorithm_explained.md)。 |
| 为什么不是按正文相似度合并？ | 不同来源或版本即使同文也要保留；不同正文不能静默覆盖。键含document/source/version/chunk/content。 |
| 时间、空间复杂度？ | add哈希O(identity UTF-8长度)+任务列表查重O(T)，哈希表查找期望O(1)。Memory含唯一证据、来源正文/定位/facts与任务关联，不能忽略source_text。 |
| off为什么不是历史B3直接复用？ | B4-off在同模型/cache-off条件下有独立真实观察；历史B3只作基线，不复制成新推理结果。 |
| 为什么B4优于off？ | Final QASPER input失败47→14，SciFact80→11，更多任务可进入后续生成。机制与指标一致，但不能推断每个质量变化都由冗余唯一导致。 |
| 为什么仍低于B1？ | QASPER B4失败53/108、B1 39/108；SciFact B4总预留失败55/100，B1无该类失败。复杂链调用/上下文/反馈成本更高，去重没有解决这些全局瓶颈。 |
| 可以称“Critic核实答案”吗？ | Critic与回答共用同一1.5B模型，反馈可能犯同样错误；只说“模型反馈驱动Replan”，不能说独立审核。 |
| CitationVerifier保证什么？ | ID、来源注册、版本、正文span及SHA一致；语义support仍unknown。不能把定位有效称为引用语义正确。 |
| F1和Accuracy是否能平均？ | 不能。QASPER是词级Answer F1/段落Evidence F1；SciFact是项目Label Accuracy和官方式rationale micro计数，分别报告。 |
| 运行完成等于正确吗？ | 不等于。Final SciFact B4 completed16，但正确Label仅2；insufficient_evidence13另计。 |
| “Token预算耗尽”是哪一种？ | native input超过3072、输出512截断、保守预留超过24000不同。每次reserve=(native input+512)×2，不等于已实际消费。 |
| 可以反复修JSON直到答对吗？ | 不能。正常失败和已保存技术失败保持终态；有限transport retry遵循原协议，恢复只解决无终态中断。 |
| RAM下降是否内存泄漏？ | 单凭Available下降不能证明。旧backend有有界8 GiB prompt RAM cache；用其日志、缓存状态和后续过程采样定位配置缺口。 |
| Cache=off为什么曾仍涨RAM？ | Agent工具cache与llama.cpp prompt RAM cache是不同层，后端需要显式--cache-ram 0。 |
| 本人哪些工作独立完成？ | 如实引用[归属说明](development_attribution.md)中已确认项；Git author和项目最终产物不能证明全部手写。 |

## 冻结Final结果与成本

| Benchmark | 组 | 固定n | Answer F1 / Label Accuracy | Evidence F1 / sentence-label micro F1 | runtime failed |
|---|---|---:|---:|---:|---:|
| QASPER | B1 | 108 | 0.1672 | 0.1345 | 39 |
| QASPER | B4 | 108 | 0.1345 | 0.1221 | 53 |
| QASPER | off | 108 | 0.0747 | 0.0871 | 68 |
| SciFact | B1 | 100 | 0.1000 | 0.0524 | 3 |
| SciFact | B4 | 100 | 0.0200 | 0.0141 | 71 |
| SciFact | off | 100 | 0.0000 | 0.0000 | 89 |

QASPER B4−off Answer F1差+0.0598、95%簇区间[0.0246,0.1016]；B4−B1差−0.0327、区间[−0.0959,0.0279]。SciFact B4−off Accuracy差+0.0200、区间[0.0000,0.0510]；B4−B1差−0.0800、区间[−0.1346,−0.0300]。无p值，不把有利的一组区间泛化成“复杂Agent全面优于直接RAG”。

Final QASPER B1/B4/off的实际报告Token总数分别233696/421391/292422，均任务延迟2.214/2.805/2.088秒；SciFact为140277/714763/684550、0.898/3.206/3.099秒。模型调用均值分别为QASPER 1/2.53/2.09、SciFact 1/4.39/3.83。输入失败减少并没有使Final B4总成本低于off或B1。

## 两个真实Dev故障案例

`Dev案例A`由off输入3354拒绝，变为on第二轮Answer输入2954获准；下一次Critic输入2973要求再预留6970，已有23950，因30920超过24000停止，实际Token只有9628。这说明input瓶颈转移到保守总预留，并没有修改预算数学。

`Dev案例B`的off得到正确CONTRADICT；on首次同标签但Critic不足，Replan后正向标签没有引用，被empty_list拒绝，正确性反而下降。这里只能证明模型反馈及输出契约变化，没有人审支持“证据语义错误”。原始Trace/SHA见本地逐题Dev审计（公开候选不附）。

## 面试前本人应能实际完成

用自己的话画出端到端链路；手算一次BM25与保守Token预留；解释EvidenceStore为何保留不同来源/版本；打开一条失败Trace核对阶段/usage与SHA；区分Available、Committed、Private Bytes、Working Set和设备显存；说明Final为何不能回流调参。这里只列待自检技能，未声称用户已经通过这些练习，也不在本次启动模型。

## 归属、局限与来源

Codex辅助的实现、测试、命令执行和文档不能自动归为本人手写；用户在会话中明确了方向、授权、安全与真实性边界并审阅阶段报告，实际独立编程/推导/逐题审核范围仍待本人确认。没有第二审核者，没有独立语义人审，也没有训练LLM。

单seed11，公开数据预训练污染未知，QASPER为可定位文本子集，SciFact项目Final是官方公开dev而非隐藏test。B5小样本、硬件/冷加载来源局限、失败分母必须保留；后续研究另注册，不用本Final重新选策略。

指标来源：[公开聚合证据](release_evidence.json)（原始报告保留本地）；原始本地审计JSON SHA256 `4c174d27f7d140dd019bd294065c716368a6bc547ef0f51b8f58fa6add99f5a2`，输入来源锁`106f2de939770df44b30dd297cc02bb9a0e8756a59b907e34573470030479144`。
