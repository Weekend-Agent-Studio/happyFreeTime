# S-CORE1C：PlanSpec 收敛与 Resume V2 回归验收

- 日期：2026-10-02
- 分支：`codex/s-core1a-plan-spec`
- 评测代码提交：`52fd353`（`refactor: complete canonical plan spec pipeline`）
- 评测时：`git_dirty=false`
- 冻结集：36 条 reviewed；Fixture SHA256 `e650bf04d01701a358981e21e4899c57bc8f16a4248f7f83cabd1051f8106422`
- Dataset SHA256：`751b8ee9fba08302467c59538376454492acc81d8f07be32a0afd4c009e18160`

## 1C 收口内容

- 用户显式结构、LLM `PlanStructureProposal v3` 和 Rule baseline 都收敛为 `PlanSpec`；唯一结构编译入口是 `PlanSpecCompiler`。Rule 形状生成是其内部私有工厂，不再有 Skeleton registry 或转换 Adapter。
- LLM 软语义仍投影到 `PlanningIntent`，结构与软目标不重复存两份；optional slot 只在 Wire Proposal，编译后展开成 concrete PlanSpec。
- 首选结构在本地搜索或 Route/Availability/Verifier 无解时，最多触发一次完整 Rule 恢复：重新使用 Rule 语义、角色检索/排序和 Rule PlanSpec，再经同一验证路径；不再调用 LLM，且首选尝试保留有限 Provider 预算。
- Compiler 决定结构是否合法；搜索与 Scheduler 判断组合/时间线；Provider/Verifier 判断具体方案是否有外部事实和硬约束违规。编译通过不等于最终方案通过。
- `Plan.skeleton_id` / `SkeletonSearchTrace.skeleton_id` 是历史命名，值是 `PlanSpec.spec_id`；没有独立 PlanSkeleton 领域模型、注册表或旧/新结构转换链。Legacy Search 仍是共享同一 PlanSpec 的独立算法模式，不是另一套结构合同。

## Frozen C0–C4

每个变体均为 36 条、单次重复，`router_model_calls=0`。C0/C2/C1/C3/C4 的最终有效结果：

| 变体 | 任务成功 | 必需断言 | 硬约束 | 冲突归因 | 修改链路 | 备注 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| C0 Rule + Rule | 34/36 | 206/208 | 7/7 | 4/4 | 10/10 | 两条语义证据断言失败 |
| C1 LLM Intent + Rule | 34/36 | 198/200 | 7/7 | 4/4 | 10/10 | objective recall 17/17；无 fallback |
| C2 Rule + Hybrid | 36/36 | 208/208 | 7/7 | 4/4 | 10/10 | Hybrid 补齐 C0 的两条语义证据缺口 |
| C3 LLM Intent + Hybrid | 36/36 | 208/208 | 7/7 | 4/4 | 10/10 | objective recall 17/17；无 fallback |
| C4 LLM + Hybrid + Advisor | 36/36 | 208/208 | 7/7 | 4/4 | 10/10 | Advisor 接受 21/27（77.8%），其余 6 次安全拒绝并回退 |

C4 接受样本的方案 ID、需求 grounding 均为 21/21；被拒绝的 Advisor 输出未改变已验证计划。Advisor 调用 27 次，输入/输出 Token 为 101,965 / 18,772，模型延迟 P50/P95 为 2,575/3,377ms。接受率比旧运行低，拒绝原因包括证据绑定、动态数字事实和 unsupported claim；应报告本轮实测，不能沿用旧的 24/27。

C2 Hybrid 稳态检索 P50/P95 为 143/207ms；本次进程首次加载模型约 90.8 秒，单独视为冷启动，不并入稳态值。

## Live B0/B3

Live 每组单次 36 条，不是冻结语义输入的确定性对照：

| 变体 | 任务成功 | 必需断言 | 硬约束 | 冲突归因 | 修改执行 | 总链路 P50/P95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| B0 Live Router + Rule 下游 | 26/36 | 155/168 evaluable | 7/7 | 4/4 | 8/9 evaluable | 1,575/2,103ms |
| B3 Live 全链路 + Advisor | 30/36 | 181/189 evaluable | 7/7 | 4/4 | 9/9 evaluable | 2,302/11,933ms |

B0/B3 的成功率低于本分支冻结 C3/C4，并且低于先前一次 Live 观测（B0 29/36、B3 32/36）。它们属于实时模型的单次结果，主要失败落在 Router 输出与预期任务分流/提问不一致；冻结 C0–C4 未显示 PlanSpec/Rule fallback 回归。不要把 Live 单次成功率包装成稳定提升；要作模型质量结论需重复运行或逐案例复核。

B3 Advisor 接受率 20/23（87.0%），3 次被 grounding Harness 安全拒绝后回退；Advisor P50/P95 2,585/3,418ms。B3 Hybrid 冷启动约 32.2 秒，稳态检索 P50/P95 123/265ms，冷启动分开报告。

## 环境与报告口径

- C2 第一次运行错误地使用了 worktree 默认空索引，出现 27 次 `index_unavailable`；该报告作废。有效 C2 显式指向本机已存在的 BGE 模型与索引后重跑，通过 36/36。C3/C4/B3 同样使用该显式本地路径。
- C1/C3 沙箱内首次运行因网络阻断出现 `network_error`；这些运行只作环境诊断，不作模型指标。经一次有界联网重跑后，C1/C3 正式结果均无网络 fallback。
- C0–C4 报告及逐案例 JSONL 留在本地 `artifacts/evals/S-CORE1C_*_20261002/`（该目录被 Git 忽略）；本文件记录可公开引用的汇总、哈希和口径。
- 评测代码提交 `52fd353` 的后端验证为 `451 passed, 34 subtests`，`compileall app tests` 通过。文档收口提交另记于本文件最终 Git 历史。

## 可引用结论与限制

1. 在相同 reviewed Frozen 输入下，C0→C2 显示 Hybrid Retrieval 补上两条语义证据缺口；C1→C3 显示 LLM Intent 与 Hybrid 组合后通过全部断言。不是生产成功率。
2. Rule fallback 已切回完整 Rule 语义与角色检索，而不是只替换结构；冻结 C3/C4 没有任务失败。
3. Live B0/B3 是单次端到端样本，结果会受 Router 输出影响；当前不用于稳定成功率或版本提升声明。
4. Advisor 的拒绝是安全行为，不影响 C4 的计划完成；但质量收益需单独盲评，接受率不等于解释质量。
5. `Plan.skeleton_id` 与 Search Trace 的历史命名仍在；代码语义与取值已是 PlanSpec ID。它们不是旧结构模型，但将来若清理外部 API 名称，需要另作 API 变更。
