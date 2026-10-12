# B4算法讲解：完整来源身份去重及其局限

## 要解决的问题

Planner为同一问题产生多个查询，Replan又可能再次命中同一原文片段。若每次命中都加入Memory，即使单子任务selector限制没有变化，全局回答、Critic和Replan的上下文仍可能包含重复内容。完整B1/B2/B3 Dev的B3有772项证据，其中181项精确重复（23.45%）和42次Token预算失败。该计数命中预登记规则，所以B4选择source_identity_dedup；这些是Dev观察，不是Final调参依据。

B4复用既有EvidenceStore组件，并非发明BM25、去重算法或Token最优选择。实验设计与结果见[Experiments](experiments.md)。

## 代码实际做了什么

执行位置是`src/deepresearch/memory/store.py:EvidenceStore.add`。身份键的原始字符串为document_id、source URL、source_version、chunk_id和content，用NUL分隔后计算完整SHA256。没有空白、大小写或Unicode归一化。

```text
identity = SHA256(document_id ⊕ NUL ⊕ source ⊕ NUL ⊕ version ⊕ NUL ⊕ chunk_id ⊕ NUL ⊕ content)
if deduplicate and identity already exists:
    keep first EvidenceItem and evidence_id
    append task_id only if not already associated
else:
    create new EvidenceItem with source/hash/span fields
    append in first-occurrence order
```

例如q1/q2都读到同一版本的paper:p3：on中Memory保留一个条目、task_ids包含两个任务；off生成两个不同Evidence ID。不同URL、版本、document、chunk或正文仍独立保存，重复正文来自两篇论文也不会合并。对同一chunk更改正文时保留新身份，不静默替换第一条证据。公开模型输入沿用原有query/evidence格式；被合并的重复正文少了，但未压缩元数据或改变JSON引用协议。

现有CitationVerifier随后可检查证据是否匹配源注册、正文span、原文/片段SHA。它不检查每个声明是否被科学语义支持；默认semantic_judge=None，support未知。Critic也与回答共用同一1.5B模型，只是工作流反馈，不是独立Gold或人工裁判。

## B4-on/off的单因素控制

| 条件 | Planner/Memory/Critic/Replan | Memory deduplicate | 其他 |
|---|---|---|---|
| B4-on | 开启，Replan最多一次 | true | 固定 |
| B4-off | 与on相同 | false | 固定 |
| B1 | 单次直接检索回答 | false | 独立基础系统条件 |

`src/deepresearch/runtime/execution.py:configuration`设置开关，ResearchEvidenceAgent将它传入EvidenceStore。on/off之间没有同时启用相邻检索、排序、selector去重、metadata压缩或新的Token预算选择。官方Qwen2.5-1.5B Q4_K_M、cache-ram=0、ctx4096、input3072、output512、total预留24000、seed11、温度0、工具18、模型调用8及deadline120秒相同。B4-off有独立真实推理记录，不能把历史B3复制成新的off输出。

B1与B4不构成只关闭dedup的消融：B1还没有Planner/有界Critic/Replan链。比较B4和B1是在比较整体系统，不能把其质量差完全归于某一模块。

## 时间与空间复杂度

设L为本次identity各字段UTF-8总字节，T为该唯一证据已关联任务数。每次add需拼接/编码/哈希O(L)，字典查找期望O(1)，task_id在列表中查重O(T)，故为O(L+T)。新建条目复制的是字段引用，不能把add当作对全部正文的深拷贝。去重键使用完整SHA256，短前缀仅用于显示Evidence ID。

Memory空间含U个唯一证据条目、任务关联、来源正文source_text、metadata/facts以及哈希键。若按实际保留字节表示，为O(B+P+A+U)，其中B是唯一片段内容，P是保留原始来源正文/metadata/facts，A是任务关联总数；Python可能共享字符串引用，不能将P无条件当零。QASPER reader中同论文全文可能共享；SQLite按chunk读取且缓存32项，不同chunk仍可能保留相同摘要字符串的不同对象。on仅避免重复身份条目的重复驻留，不保证全项目RAM减半。

当前子任务、工具、Replan预算为Memory规模提供上界。23.45%是Dev存储项重复比例，不是Token节省率、复杂度加速倍数或语义覆盖率。

## Final的真实结论

| Benchmark | 指标 | B1 | B4-on | B4-off |
|---|---|---:|---:|---:|
| QASPER，108题 | Answer F1 | 0.1672 | 0.1345 | 0.0747 |
| QASPER，108题 | paragraph Evidence F1 | 0.1345 | 0.1221 | 0.0871 |
| SciFact，100条 | Label Accuracy | 0.1000 | 0.0200 | 0.0000 |
| SciFact，100条 | sentence-label micro F1 | 0.0524 | 0.0141 | 0.0000 |

on相对off的Final QASPER Answer F1差为+0.0598，配对95%簇区间[0.0246,0.1016]；SciFact Accuracy差+0.0200，区间[0.0000,0.0510]。使用2000次重采样、seed20261009、108论文簇/95来源连接簇。单seed公开子集与很低绝对分数不能支持普遍收益、SOTA或引用语义准确率提升。

## 为什么高于off，却没有超过B1

观察到的瓶颈变化：QASPER off→on input失败47→14、总运行失败68→53；SciFact input80→11、总运行失败89→71。去重确实使更多原本被输入门槛挡住的请求进入后续链，这与机制相容。完整任务质量还受模型生成、引用输出及同模型反馈影响，不能仅凭input下降归因全部答案改善。

相对B1，B4没有解决链路的总体开销：QASPER on仍有14次input、3次总预留、36次incomplete运行错误，B1失败39次；SciFact on有55次总预留失败和13次insufficient_evidence，而B1只有3次empty_list失败。模型调用均值为QASPER B1 1、B4 2.53；SciFact B1 1、B4 4.39。Final B4的实际报告Token总数为QASPER421391、SciFact714763，B1为233696/140277，off为292422/684550。被输入拒绝的任务继续走到Answer/Critic/Replan，可增加实际消费；小输入不等于低全任务成本。

这些证据支持“局部冗余控制尚未解决总预留与生成稳定性瓶颈”。规划质量差、同模型自评不可靠或证据噪声可能影响表现，但没有独立逐题语义标注，不能把它们直接认定为全部失败的已证实原因。B4在Final两个Benchmark均未超过B1，后续不能据此回流修改已冻结方法或再采样失败题。

## 30秒讲解与说法边界

“B4用完整来源身份合并跨查询的同一证据，保留不同来源/版本和首次引用ID。它是已有组件的受控消融。Final QASPER F1高于off但低于B1，SciFact仅2/100条Label正确；重复上下文减少后，一部分失败转移到总预留或生成协议，全任务成本反而增加。该项目提供受控算法对照与负结果，不能声称独立原创或全面提升。”

当前不存在独立人审语义标签，Citation Support Rate、Unsupported Claim Rate保持null。

实现来源：`src/deepresearch/memory/store.py`、`src/deepresearch/agent/research.py`、`src/deepresearch/runtime/execution.py`、`src/deepresearch/critic/citations.py`。结果来源：[公开聚合证据](release_evidence.json)与[Experiments](experiments.md)。复现范围和指标核验边界见[Limitations](limitations.md)。
