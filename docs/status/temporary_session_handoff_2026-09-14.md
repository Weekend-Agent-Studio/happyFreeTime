# HappyFreeTime 临时会话交接文档

> 生成日期：2026-09-14  
> 适用范围：本文件总结本次临时侧边会话中完成、检查、修正和验证过的工作，供下一次会话继续使用。它不是新的 Roadmap，也不替代代码和测试；凡是“当前状态”都应以仓库实际文件、Git 状态和评测产物为准。

## 0. 先看结论

### 0.1 当前项目已经是什么

[代码事实]

HappyFreeTime 已经不是只会返回一段文本的简单 Workflow。当前主链是一条“受限语义决策 + 确定性规划和校验”的混合链路：

- Router/RouterExtractor 负责把一轮消息路由为创建、修改、澄清、冲突等意图。
- PlanningIntent 有 RuleBased 和 LLM 两个 Adapter；LLM 只能在有限语义合同内提议节奏、角色、站数、顺序和软目标。
- Candidate Retriever 支持 RuleBased 词法基线和本地 BGE Hybrid Retrieval。
- Catalog、路线、天气、营业、Availability 和 Verifier 仍由代码及 Provider 边界负责。
- 失败、超时、非法输出和低置信会回退到规则路径或返回结构化失败。
- 方案、选择状态、Plan Version、单站替换和 PlanDiff 已经持久化。
- Recommendation Advisor 支持确定性解释和受证据约束的 LLM 解释。
- S5 的离线评测 Runner MVP 已存在，能用 TestClient、SQLite 和固定 Provider Fixture 跑 HTTP 链路并生成逐项断言报告。

[合理推断]

它已经足够作为“受约束 Agent 应用”的工程原型进行面试和投递，但还不能把当前所有评测数字直接写成正式效果指标。原因不是链路不存在，而是完整消融矩阵尚未跑完，且真实模型输出存在波动和 fallback；历史报告中的 draft 状态已经在后续审核中收口。

### 0.2 当前最重要的事实

[代码事实]

- 当前 Git 分支：codex/m2-s0-native-planning。
- 当前 HEAD：83366df fix: stabilize real llm evaluation path。
- 最近关键提交依次包括：
  - 7a0247e：版本化 POI 语义 Profile 与本地 Embedding 基础设施。
  - bb71f1e：BGE Hybrid Candidate Retrieval。
  - 1b5139f：隔离 Retrieval 评测缓存并拆分延迟指标。
  - b5e9d3b：受约束 Recommendation Advisor。
  - 83366df：稳定真实 LLM 评测路径。
- 当前工作树不是干净状态，存在大量已修改文件和若干未跟踪文件。不要 reset、checkout 或删除它们。
- 本次侧边会话没有产生新的 commit。

[建议]

下一次会话开始时先记录 status 和 log，再按文件职责检查和分组提交。不要把临时评测产物、API Key 或未审核的数字写进简历。

## 1. 本次会话解决和确认的问题

### 1.1 Availability 三态语义

[代码事实]

系统现在区分：

- 已确认可用：可作为正常候选。
- 已确认不可用：不能进入最终方案，或触发有界替换。
- 未知、缺失、过期或因预算耗尽没有确认：普通请求可以返回，但必须带 warning；用户明确要求“确认有位/必须可预约”时，未知状态会阻塞并产生结构化冲突。

领域约束中增加了 require_availability_confirmation。Demo Router 能识别“确认有位、必须有位、确认可预约”等表达，Enrichment 会保留该约束，Verifier 会根据严格标志决定 warning 或 blocking violation。

[业务意义]

“不可用”可以代表景点当天闭馆、餐厅当时不可预约、库存或名额明确耗尽等。真实系统里应来自营业、库存或预订 API；当前 Demo 中来自固定 Fixture/Provider，不是真实实时数据。已确认没位置的条目不能作为可执行推荐；未知状态可以作为参考，但必须显式标注不确定性。

### 1.2 冲突码优先级和测试用例澄清

[代码事实]

曾有用例期待 DEPARTURE_NOT_BEFORE_RETURN_BY，但代码先返回 DEPARTURE_OUTSIDE_TIME_WINDOW。原因是出发时间同时违反了返程截止和原先推断的下午时间窗，两个冲突都合理，优先级却没有写清楚。

当前用例已经改成无歧义表达：

- 用户：“下午五点半准时出发，最晚17:00回家”
- 预期：DEPARTURE_NOT_BEFORE_RETURN_BY

[建议]

以后增加冲突案例时，应只制造一个主要冲突，或者在数据合同中明确稳定的冲突优先级。不要让评测标签依赖实现细节偶然排序。

### 1.3 精确出发时间优先于模糊时间段

[代码事实]

Enrichment 现在把用户明确说出的时间当作更高优先级：

- “下午三点二十准时出发”解析为 15:20。
- 模糊的“下午”只用于补充宽泛窗口；一旦存在精确出发时间，不再用 14:00–18:00 的推断窗口覆盖它。
- 若用户同时给出显式范围，例如 10:00–16:00，范围仍受保护；精确时间落在该范围之外时仍可冲突。
- 没有返程截止时，会使用有限的默认规划时长；有 return_by 时保持独立。
- 已增加中文分钟和中文返程时间解析，例如“下午三点二十”“晚上八点前回来”。

[业务意义]

用户说“下午六点半出发”通常是明确的 18:30，而不是“下午窗口写错了”。系统应优先尊重明确时间；只有在和显式范围、返程截止或其他硬约束真正矛盾时才阻塞。

### 1.4 Router 的真实调用边界

[代码事实]

Router 的生产模型调用现在有：

- HFT_ROUTER_TIMEOUT_SECONDS，默认 15 秒。
- max_retries=0，避免 LangChain 内部重试把调用次数和延迟放大。
- timeout、model_error、invalid_output 分开记录。
- API Key 不进入报告。

当前仍保留 Demo Router。HFT_DEMO_MODE=1 时强制离线；HFT_DEMO_MODE=0 或有 LLM_API 且未强制 Demo 时，才会走真实 Router。

### 1.5 S2 PlanningIntent 的降级策略

[代码事实]

PlanningIntentProvider 的默认行为是 RuleBased。LLM 模式只是可选 Adapter：

- 一次结构化调用。
- 解析错误可进行一次格式修复。
- 第二次失败、超时、异常、低置信、非法角色/站数/precedence 时，回退到 RuleBased。
- LLM 不能决定路线、天气、预算、营业、Availability、resource_id 或 Verifier 结论。
- evidence 先经过证据一致性过滤，不能把模型凭空编出的偏好写入最终语义状态。

[建议]

面试表达应说“模型提案受到 Schema、证据、预算和确定性编译约束，并有 RuleBased fallback”，不要说“LLM 直接负责规划”。

### 1.6 S3 轻量 Hybrid RAG

[代码事实]

已完成的本地检索基础设施：

- 30 个重点 Demo POI 的版本化 PoiSemanticProfile。
- Profile 只包含稳定、可溯源的语义资料，禁止混入路线距离、当前天气、营业状态、库存和未知来源的真实用户评价。
- 没有人工 Profile 的 POI，会由名称、分类、标签和地址生成基础 Profile；这不是高质量人工语义证据。
- 200 个候选、504 个 chunks 的本地索引。
- bge-small-zh-v1.5，512 维，归一化向量。
- npz 向量文件和 manifest，记录 Profile source hash、模型、维度、chunk 数量和失效信息。
- RuleBasedRetriever 是默认和降级基线；Hybrid 使用词法、稠密向量和 objective 证据融合。
- Retriever 只在 Catalog 硬过滤后的候选内排序，不负责路线、天气、营业或 Availability。
- 创建方案和单站替换共用 CandidateRetriever。
- 缺模型、索引、依赖或加载失败时，必须明确回退 Rule，不伪装成 Hybrid 成功。
- 前端和 Trace 能显示实际 Adapter、索引版本、查询数、候选数、延迟和 fallback 原因。

[历史评测事实]

一次早期 18 条固定样例的对照结果为：

| 指标 | RuleBased | BGE Hybrid |
| --- | ---: | ---: |
| Recall@3 | 0.186 | 0.431 |
| Recall@5 | 0.186 | 0.490 |
| nDCG@5 | 0.147 | 0.580 |
| MRR | 0.196 | 0.796 |
| P50 | 3.5 ms | 61.5 ms |
| P95 | 4.3 ms | 2296.4 ms |
| fallback | 0 | 0 |

这次结果只能说明在当时的小型 Fixture 上存在可观察收益；不能直接写成生产效果。当前 holdout 文件是 16 条，其中 15 条 metric_eligible=true，另有 1 条只用于 grounding safety。当前检索 holdout 标签已写为 reviewed，但仍应在重新运行后检查数据版本和结果是否可比。

### 1.7 S4 证据化推荐解释

[代码事实]

Recommendation Advisor 已支持：

- RuleBased 确定性解释。
- LLM 单次调用加一次格式修复。
- 只能引用已经验证的方案、用户需求证据、召回证据和 PlanDiff。
- Harness 校验 Plan ID、Evidence ID、matched need ID 和数字事实。
- 违规、超时或证据不支持时回退 RuleBased。
- 创建和单站替换的解释都接入 API、Trace、会话恢复和幂等重放。
- UI 展示“本轮理解、最推荐方案、适合你的原因、主要取舍”。

默认配置仍为：

    HFT_RECOMMENDATION_ADVISOR_MODE=rule

因此平时前端测试不会消耗 DeepSeek 流量；只有明确切换到 llm 并配置 LLM_API 才会调用模型。

### 1.8 S5 评测 Runner MVP

[代码事实]

已加入或修改：

- app/evaluation/resume_release.py
- evals/run_resume_release_eval.py
- tests/test_resume_release_eval.py
- tests/test_resume_release_eval_http.py
- evals/resume_release_dataset.py
- evals/resume_release_cases.json
- evals/retrieval_holdout_cases.json
- evals/run_retrieval_eval.py
- evals/README.md

Runner 的关键边界：

- 通过 create_app 和 TestClient 运行，不启动 Uvicorn，不依赖运行中的前端。
- 每条 Case 使用独立临时 SQLite，避免 session、request_id、Plan Version 和并发污染。
- 固定 evaluation clock、Catalog、route/weather/geocoding/availability Fixture。
- 每条 Case 按 steps 顺序执行真实 HTTP 消息、select_plan 和显式结构化 replace_stop。
- 保存 Transcript：请求、响应、plans、Plan Version、PlanDiff、Advice、PlanningIntent、retrieval evidence、RuntimeDecision、延迟和错误。
- 不保存 API Key、Authorization Header、完整模型 Header 或 .env 内容。
- 评分按断言拆分，不把 HTTP 200 等同于任务成功。
- token 为 null 时是 unknown，不能当 0。
- fallback 成功与正常成功分开统计。
- artifacts/evals/ 已加入忽略，原始运行报告不应进入 Git。
- 当前 Runner 是 S5-MVP，不等于完整的 Resume Release 评测和最终简历指标。

### 1.9 2026-09-14 窄修复

[代码事实]

- Recommendation Advisor 的模型上下文现在会在每个方案旁边提供确定性基线生成的
  allowed_matched_need_ids、allowed_supporting_evidence_ids 和对应证据摘要。
- 全局 retrieval_evidence 仍保留用于审计，但 Prompt 明确它不是任意方案的自动证据；原有
  Harness 的 Evidence/Plan 校验没有放宽。
- 评测 Runner 在 transcript 中新增 semantic_stage_trace，仅记录 TurnInterpreter 的语义字段
  和最终 PlanningIntent 的 objective/query/evidence 摘要，不修改 AgentResponse、Graph 状态或业务表。
- 定向测试结果：Recommendation Advisor、Resume Release Eval 和 HTTP Eval 共 21 passed；
  compileall 与 git diff --check 通过。

[仍待验证]

这次修复降低了 B3 错引证据和 B2 语义归因不清的概率。追加 Pilot 已运行，但 B2 未进入下游，
B3 仍出现 Planner 语义漏检和 Advisor invalid_proposal；必须先完成失败归因，再决定是否继续修。

## 2. 当前端到端链路

### 2.1 创建规划

用户输入消息后，当前业务链路可概括为：

    HTTP POST message
      -> Entry Graph / Router
      -> DemoRouter 或真实 RouterExtractor
      -> Enrichment（补齐时间、角色、硬约束和证据）
      -> PlanningIntentProvider（RuleBased 或 LLM）
      -> 合法 PlanningIntent 编译为有限骨架
      -> Catalog 硬过滤
      -> CandidateRetriever（Rule 或 Hybrid）
      -> 确定性 Planner 组合候选
      -> Route / Weather / Geocoding / OpeningHours / Availability Provider
      -> Verifier
      -> 最多两轮有界 Repair
      -> RecommendationAdvisor
      -> 原子写入 Plan、PlanVersion、SessionSnapshot
      -> AgentResponse + Trace
      -> React 工作台展示

模型参与的位置是 Router、可选 PlanningIntent 和可选 Recommendation Advisor。路线、天气、预算、营业、可用性和最终是否可执行由 Harness 裁决。

### 2.2 单站替换

S3.5 的修改链路不是“把整个旧对话重新喂给模型再自由规划”：

    前端选择当前已选方案的某一站并点击换这站
      -> 发送结构化 ConversationCommand
      -> 校验 session、active Plan Version、base plan、target stop、resource_id、role
      -> 非目标站作为锁定 identity
      -> 只对目标角色调用 CandidateRetriever
      -> 重新执行路线、营业、Availability、Verifier 和有限 Repair
      -> 生成最多两个候选
      -> 每个候选生成自己的 PlanDiff
      -> RecommendationAdvisor 解释候选和 diff
      -> 创建不可变新 Plan Version
      -> active version 切换，旧 selected plan 清空

当前支持 1–4 站总方案中的任意单站替换；它不是只支持“两站”。每次前端操作是一次一个目标站，多站修改可以通过多轮完成，但尚未承诺任意骨架重排。

### 2.3 解释链路

Advice 不是把任意文本交给 LLM 后直接显示：

- Rule Advisor 可以从结构化事实生成模板。
- LLM Advisor 只能从已验证 Plan、需求证据、召回证据和 PlanDiff 中选择和组织解释。
- 数字事实（距离、费用、时间差等）由代码渲染或校验。
- Advice 缺证据时允许 fallback；这会被记录为 degraded completion，而不是伪装成普通成功。

## 3. 评测当前真实结果

### 3.1 Offline Sanity

最近一次完整离线运行：

- 产物目录：artifacts/evals/offline_sanity_20260913T160632Z/
- Run ID：20260913T160651Z-664d79ef
- 35 条 E2E Case。
- 这份历史报告生成时 35 条 label_status 都是 draft；之后已完成人工审核并将数据文件更新为 reviewed。
- task success：13/35。
- hard constraint Case：4/7。
- required assertions：137/191。
- opening hours：23/23。
- weather safe：1/1。
- no verified unavailable resource：23/23。
- availability observation coverage：7/23；这是诊断覆盖率，不是质量通过率。
- product_failure：22。
- runner_failure：0。
- model_failure：0。
- label_review_required：35。

[重要解释]

这不是“系统随机坏掉，只成功了 13 条”的最终结论。它表示 35 条全部参与了 Runner，但当时有很多 Case 超出离线 RuleBased 能力、依赖尚未启用的模型语义、或仍需要重新确认标签/Fixture 合同。它很有价值，因为它暴露了边界；由于该历史报告生成时 label_status 全是 draft，不能拿 13/35 当简历端到端成功率。

### 3.2 真实 DeepSeek Pilot

为了验证“模型是否真的被调用”，曾使用本地 .env 中的 DeepSeek 配置完成了四个单 Case Pilot。API Key 没有写入文档和报告。

| Variant | 结果 | 关键观察 | tokens |
| --- | --- | --- | --- |
| B0_DOWNSTREAM_RULE | 任务失败 | Router 真实调用成功；Rule PlanningIntent 没识别 romantic 和 low_fatigue，说明下游规则基线确实暴露语义缺口 | input 7482，output 836 |
| B1_LLM_INTENT | 1/1 成功 | LLM PlanningIntent 识别 romantic 和 low_fatigue；无最终 fallback | input 9398，output 1348 |
| B2_HYBRID_RETRIEVAL | 方案返回，但 romantic 漏掉、low_fatigue 命中 | BGE Hybrid 实际加载，adapter=bge_hybrid，index version=retrieval-index.v1；无最终 fallback | input 9275，output 1178；token coverage 2/3 |
| B3_GROUNDED_ADVICE | 完成但 degraded | Advisor 实际调用过，但因 unsupported_plan_evidence 回退；grounded advice 断言仍通过 | input 12900，output 2375；token coverage 0.75；fallback 1/4 |

报告目录：

- artifacts/evals/B0_DOWNSTREAM_RULE_20260913T161055Z/
- artifacts/evals/B1_LLM_INTENT_20260913T161135Z/
- artifacts/evals/B2_HYBRID_RETRIEVAL_20260913T161237Z/
- artifacts/evals/B3_GROUNDED_ADVICE_20260913T161557Z/

[结论]

DeepSeek 不是“从来没调用过”。这四次报告已经证明 Router、PlanningIntent、BGE Hybrid 和 Advisor 的运行时决策能被记录，且模型失败会真实进入 fallback。Pilot 规模只有每个 Variant 一个 Case，不能作为最终 A/B 结论。

曾有一次真实网络运行返回 WinError 10013，这是沙箱网络权限问题，不是模型逻辑失败；在允许网络后同一类 Pilot 成功运行。以后遇到网络权限错误，要归类为环境/runner 阻塞，不要修改业务逻辑掩盖它。

### 3.3 窄修复后的追加 Pilot（2026-09-14）

[代码事实]

- B2 第一次追加运行：在 TurnInterpreter 层以 model_error 结束，耗时约 3.1 秒，未进入
  PlanningIntent 或 BGE；报告目录为 artifacts/evals/B2_HYBRID_RETRIEVAL_20260914T051629Z/。
- B2 第二次在允许网络后运行：仍在 TurnInterpreter 的第二次调用后回退，token 已观测到
  input=7482、output=1039；未进入后续阶段；报告目录为
  artifacts/evals/B2_HYBRID_RETRIEVAL_20260914T051713Z/。
- B3 追加运行完整走过 TurnInterpreter、PlanningIntent、BGE Hybrid 和 Advisor；报告目录为
  artifacts/evals/B3_GROUNDED_ADVICE_20260914T051756Z/。
- B3 的 semantic_stage_trace 显示 TurnInterpreter 的 scene_tags 为空，PlanningIntent 最终
  只有 low_fatigue；因此本次 romantic 缺失首先应归因于上游语义抽取波动，而不是 BGE 排名。
- B3 的 Advisor 调用两次提案都未通过结构/解析校验，最终 fallback_reason=invalid_proposal；
  这次没有再出现 unsupported_plan_evidence。Rule Advice 保证了 grounded_advice 断言通过，
  但整条 Case 因 romantic 断言失败，不能视为 LLM 成功。
- B3 观测到 BGE Hybrid 实际运行，opening-hours 和 no-verified-unavailable 后置断言通过；
  BGE 冷启动约 86.8 秒，使整条 Case 约 103.7 秒，不能当作稳态延迟。

[结论]

这次追加运行验证了诊断工具本身有效：可以区分“上游语义未抽取”和“下游规划未映射”。
但由于 B2 未进入下游、B3 的模型输出仍有波动，不能据此宣称窄修复已经改善模型效果。
下一步应先复核失败阶段的原始安全摘要和 Prompt/Schema 合同，再决定是否继续修 Advisor；
不要为了得到一次成功而无限重试或放宽校验。

## 4. 当前数据集和标签状态

### 4.1 E2E 数据

[代码事实]

evals/resume_release_cases.json 当前包含 35 条 Case，字段覆盖：

- 精确出发、明确返程、全天、午饭/晚饭/活动单站。
- 父母、约会、低疲劳、安静、聊天、新鲜感、少辣等软目标。
- 预算澄清和冲突。
- 雨天或室内条件。
- 显式单站替换、多轮修改、保持非目标站 identity。
- 失败和能力边界的反例。

当前 35 条的 label_status 已全部为 reviewed；逐条审核依据和过程摘要记录在 [`resume_release_case_review_2026-09-14.md`](resume_release_case_review_2026-09-14.md)。

### 4.2 Retrieval Holdout

[代码事实]

当前 evals/retrieval_holdout_cases.json 有 16 条：

- 15 条 metric_eligible=true，参与 Recall/nDCG/MRR 分母。
- 1 条 grounding_safety 用例，metric_eligible=false，不进入 Retrieval 质量分母。
- 当前条目已带 reviewed、relevance、review_note 等信息。

[建议]

在正式写简历前仍需确认：

- reviewed 是否确实来自人工审核，而非只由模型生成。
- Profile、Catalog、Embedding index 的 source hash 和数据版本是否与报告一致。
- 修改过 POI 类型或 Profile 后，旧报告是否需要作废并重跑。
- 不要把 16 条当成足以证明生产检索质量的规模；它适合 Demo/离线回归，正式表述应说明小型 holdout。

## 5. 尚未完成或仍有风险

### P0：投递/评测前必须处理

1. **工作树没有收口提交。**  
   当前有业务代码、评测代码、数据、文档同时 dirty。下一次会话必须先区分哪些是本次修复、哪些是之前已有工作，再有选择地提交；不要 reset 或删除用户文件。

2. **35 条 E2E 标签已冻结，但仍需按审核记录解释分母。**
   当前全部为 reviewed，且纳入 E2E 主评测；这不等于每个 Variant 都必须通过。若未来改变产品范围，应新增审核修订，不能在跑完结果后为了好看反向改答案。

3. **完整 S5 消融矩阵未完成。**  
   当前只做过 offline sanity 和四个单 Case Pilot，没有稳定的多 Case、重复运行和正式 B0–B3 对照。不能写“LLM 让成功率提升 X%”。

4. **RuleBased 与模型能力差异需要明确归因。**  
   离线失败中有 product/capability boundary，不应混成 runner_failure 或 model_failure。要先分出支持范围内的任务分母，再谈端到端质量。

### P1：下一轮优先确认

1. **真实模型输出有波动。**  
   已增加评测 transcript 的 semantic_stage_trace 和 Advisor 的逐方案证据白名单；仍需通过新的 Pilot
   检查 B2 的 romantic 是在哪一层丢失，以及 B3 是否还因 unsupported_plan_evidence 回退。不要直接放宽 Harness。

2. **Advice 证据合同仍有可用性摩擦。**  
   已在上下文中显式暴露每个方案的 allowed evidence；B3 已证明安全校验能拦住不受支持的证据，
   但仍需确认真实模型能否按白名单引用，而不是继续回退。若仍失败，先定位 ID 映射或上下文装配。

3. **Runtime token coverage 不完整。**  
   某些 Provider 或模型响应没有 token，必须报告 unknown。只有 coverage=100% 才能把 token sum 称为完整调用量。

4. **Availability 评测观察覆盖不高。**  
   普通请求允许 unknown warning，严格确认请求必须阻塞；评测需要分别覆盖这两类，而不是简单要求所有 Case 都有 Availability 证据。

5. **最新时间解析改动后的全量回归尚未重跑。**  
   本轮只重跑过 Router/Enrichment/DemoRouter、native planning 等定向测试；完整 pytest tests 在最新改动后尚未重新执行。

### P2：可以推迟

- 订单、支付、真实库存和副作用执行。
- MCP、长期记忆、向量数据库、多 Agent、通用 Tool Loop。
- 真实高德天气/路线接入。
- LLM-as-Judge。
- 大规模 POI 数据和更多城市。
- 任意骨架重排、无限多站修改、撤销/回滚 UI。

这些不妨碍当前 Resume Release V1；现在继续加它们会把项目重新拖回架构扩张。

## 6. 面向下一次会话的推荐顺序

### Step 1：建立安全基线

只做读取和验证：

    git status --short
    git log -5 --oneline

确认：

- 分支没有被切换。
- 不要 reset、checkout 或清理未跟踪文档。
- API Key 没有进入 tracked diff。
- artifacts/evals 仍被忽略。

### Step 2：按职责检查 dirty diff

建议把变更分为四组：

1. 业务安全修复：时间解析、Availability、冲突行为、Router timeout。
2. 检索和数据：Profile、Catalog、BGE index 相关代码和 Fixture。
3. S5 评测：app/evaluation、evals runner、测试和数据集。
4. 文档：Roadmap、评测说明、面试和交接文档。

先查看 diff，再决定提交粒度。不要把本次交接文档和业务 commit 混在一起。

### Step 3：运行低成本定向检查

    .\.venv\Scripts\python.exe -m pytest tests/test_router_extractor.py tests/test_enrichment.py tests/test_demo_router.py
    .\.venv\Scripts\python.exe -m pytest tests/test_native_planning.py
    .\.venv\Scripts\python.exe -m compileall app evals
    git diff --check

如果需要对外发布稳定版本，再运行完整 pytest tests；不要为了“验收”反复调用真实模型。

### Step 4：先审核标签，再跑 Pilot

E2E 标签审核应只看用户输入、业务定义和当前产品范围，不看本次系统输出。每条标记为：

- reviewed：预期明确、当前版本有意支持。
- capability_boundary：预期合理但当前明确不支持；进入诊断，不进入产品成功率分母。
- label_review_required：预期或字段仍有歧义。
- fixture_failure：测试环境本身不满足合同。

审核决定已写回数据文件，并在 `docs/status/resume_release_case_review_2026-09-14.md` 保留 reviewer/date/依据摘要。未来新增 Case 仍必须人工审核，不能自动批量晋级。

### Step 5：跑小 Pilot，不跑完整矩阵

建议先选 6–8 条：

- plan_exact_departure_1520
- plan_all_day_date_relaxed
- plan_parents_novel_not_tiring
- plan_quiet_date_chat
- plan_dinner_only_light
- clarify_strict_budget_without_amount
- conflict_departure_after_return
- modify_activity_shorter 或 modify_dinner_less_spicy

B0–B3 各运行一次，显式设置最大模型调用数。首先只检查 Runner、Trace、token、fallback、修改保持和报告归因是否正确。

### Step 6：确认后再跑完整矩阵

只有同时满足下面条件，才值得跑 35 x 4 或重复矩阵：

- 关键标签已 reviewed。
- 失败归因没有 runner_failure。
- full run 的模型调用预算和成本已确认。
- B0–B3 的配置差异能被准确隔离。
- 指标 denominator 已明确。
- 冷启动与 warm latency 已分开。
- token coverage 的写法准备好。
- 评测失败不会自动修改业务代码、Prompt、权重或 Label。

## 7. 常用配置和命令

### 7.1 离线 UI 与真实 LLM

.env.example 当前关键配置：

    MODEL_NAME=deepseek-chat
    LLM_API=只在本机 .env 填写，不要写入 Git
    BASE_URL=https://api.deepseek.com
    HFT_DEMO_MODE=1
    HFT_PLANNING_INTENT_MODE=rule
    HFT_PLANNING_INTENT_TIMEOUT_SECONDS=15
    HFT_ROUTER_TIMEOUT_SECONDS=15
    HFT_RECOMMENDATION_ADVISOR_MODE=rule
    HFT_RECOMMENDATION_ADVISOR_TIMEOUT_SECONDS=15
    HFT_CANDIDATE_RETRIEVER_MODE=rule
    HFT_EMBEDDING_MODEL_PATH=data/models/bge-small-zh-v1.5
    HFT_EMBEDDING_DEVICE=cpu
    HFT_RETRIEVAL_CACHE_DIR=data/retrieval/cache

要在前端观察真实模型行为，至少需要：

- HFT_DEMO_MODE=0。
- HFT_PLANNING_INTENT_MODE=llm，才能让 PlanningIntent 调模型。
- HFT_RECOMMENDATION_ADVISOR_MODE=llm，才能让解释阶段调模型。
- HFT_CANDIDATE_RETRIEVER_MODE=hybrid，才能让候选排序使用本地 BGE；缺依赖或索引时会如实回退 rule。
- LLM_API、MODEL_NAME、BASE_URL 使用兼容 OpenAI 接口的本地配置。

每次修改 .env 后重启后端；不要只刷新前端。

### 7.2 S5 Runner

离线：

    .\.venv\Scripts\python.exe -m evals.run_resume_release_eval --variant offline_sanity --allow-draft

单个真实 LLM Case 的示例：

    .\.venv\Scripts\python.exe -m evals.run_resume_release_eval --variant B1_LLM_INTENT --case-id plan_all_day_date_relaxed --allow-llm --allow-draft --max-model-calls 20

首次运行建议限制 Case、Variant 和调用预算；完整矩阵前不要盲目执行。

## 8. 当前可诚实写进简历的内容

[建议]

目前可以写：

- 受约束混合 Agent：LLM 负责开放语义和受限提案，Harness 负责硬约束、事实、验证、降级和副作用边界。
- LangGraph/FastAPI/SQLite/React 的端到端状态链路。
- RuleBased/LLM PlanningIntent 双 Adapter。
- 版本化 POI 语义 Profile、本地 BGE Hybrid Retrieval、Rule fallback 和可追溯证据。
- Plan Version、选择、单站替换、PlanDiff、Verifier/Repair 和证据化 Recommendation Advisor。
- 离线可复现评测 Runner、逐项断言、Trace、fallback、延迟和 token 观测。

暂时不要写成：

- “端到端成功率 13/35”。
- “LLM 使成功率提升 X%”。
- “生产级 RAG”或“实时库存/实时预订”。
- “完全自主 Agent”。
- “支持任意自然语言重排和下单”。

更稳妥的表达是“完成了可离线回放的受约束 Agent 规划链路和评测 MVP，正在用 reviewed holdout 和 B0–B3 消融确认各组件真实收益”。

## 9. 需要下一次会话回答的关键问题

1. 35 条 E2E 已按当前 Resume Release 范围纳入；若产品范围变化，是否需要新增 capability_boundary 修订？
2. reviewed 集合上的 task success 和 hard constraint denominator 分别是多少？
3. RuleBased、LLM PlanningIntent、Hybrid Retrieval、LLM Advisor 的增量收益是否能在相同 Router 和相同 Fixture 下被分离？
4. B2 的 romantic 漏检和 B3 的 unsupported_plan_evidence 是数据问题、Prompt/Context 问题、ID 合同问题，还是合理 fallback？
5. token coverage 是否足以对外报告；若不足，是否只报告已观测调用的范围并明确 unknown？
6. 最新安全修复后完整 pytest 是否通过？
7. 是否保持 Rule 为默认、Hybrid 和 LLM 为可切换实验模式？
8. 何时冻结 Resume Release V1，停止继续扩展？

## 10. 不要忘记的总原则

- 先保护可交付版本，再扩展架构。
- 先看代码事实，再做合理推断，最后才给个人建议。
- 不把更多模型节点、RAG、MCP 或 Graph 层级自动当成先进。
- 不把失败隐藏成 fallback 成功，也不把未知 token 当 0。
- 不用系统输出反向修改答案标签。
- 不让评测推动业务 Prompt、规则或权重偷偷变化。
- 任何新设计都必须回答：它解决了哪个真实用户问题？增加了多少延迟、成本和测试负担？失败时如何降级？
- 明天如果临时会话结束，优先读取本文件和 Git 状态；不要从头重新设计项目。

## 11. 2026-09-14 最新交接补充

### 11.1 本次收口的工作树与提交状态

[代码事实]

- 本次更新前的基线仍是 `83366df fix: stabilize real llm evaluation path`。
- 当前脏树包含业务边界修正、语义中间层/模型兼容处理、S5 评测 Runner、评测数据和文档等变更；本次已获准将其作为一个检查点提交。
- `app/evaluation/resume_release.py`、`evals/run_resume_release_eval.py` 及对应测试属于评测代码，不应改变生产 Planner、Graph、数据库或前端合同。
- `app/services/llm_compat.py` 和 `app/services/model_errors.py` 用于兼容不同 OpenAI-compatible 服务并安全分类异常；报告只应保留异常类别/安全状态，不保留 Key、Header 或完整响应。
- 模型和本地 BGE 索引仍由 `.gitignore` 排除；评测原始产物位于 `artifacts/evals/`，不应提交到 Git。

[交接要求]

- 下一次会话先运行 `git status --short` 和 `git log -5 --oneline`，以实际提交结果为准，不要假设本文件中的旧 hash 仍是 HEAD。
- 若需要拆分提交，优先把评测 Runner/测试、业务安全修正、文档分别核对；不得用 reset、checkout 或清理命令抹掉用户文件。

### 11.2 S5 MVP 与最新 Pilot 状态

[代码事实]

- S5 目前是可运行的 MVP，不是完整的 Resume Release 矩阵：支持固定 Clock、Fixture、独立 SQLite、TestClient HTTP 链路、逐项断言、Transcript、failure details、fallback、token coverage 和冷/热阶段延迟。
- 评测 Runner 已能复用同一轮中的无状态/昂贵依赖（例如 BGE 和 LLM client），同时为每个 Case 保持独立 App/SQLite；第一成功的 BGE 检索标记为本轮 cold，后续标记为 warm。该标记是评测口径，不等于每次进程都发生物理模型加载。
- 最新 qwen3.8-flash B2 Pilot（`artifacts/evals/qwen38_B2_pilot_escalated/`）3 条 Case 均完成，9 次模型调用无 fallback；token 观测为 6/9，端到端 P50/P95 约 13.3s/50.7s，BGE 首次检索约 37.6s，后续约 90ms。它证明 Hybrid 路径真实运行，但样本太小，不能作正式收益结论。
- 最新 qwen3.8-flash B3 Pilot（`artifacts/evals/qwen38_B3_pilot_escalated30/`，Advisor timeout=30s）为 1/3 正常任务成功、1/3 degraded completion、2/3 失败；失败主要是 TurnInterpreter `invalid_output` 和 Advisor `invalid_proposal_contract:ungrounded_text`，不是本次的 `low_confidence`。Advisor 的安全回退仍阻止了无证据解释。
- 更早的 qwen3.8 默认 15s 运行（`artifacts/evals/qwen38_B3_pilot5/`）中，`plan_all_day_date_relaxed` 和 `plan_parents_novel_not_tiring` 的 PlanningIntent 曾因 `low_confidence` 回退；必须和最新 30s 运行分开解读，不能把不同失败码混在一起。
- 当前仍没有可用于简历的正式 B0–B3 增量结论：E2E 35 条虽已 reviewed，但 Pilot 规模很小，模型输出和 token 覆盖也不稳定。

### 11.3 `low_confidence` 的准确含义与风险

[代码事实]

- `low_confidence` 是 `PlanningIntent` LLM Adapter 的后解析门槛：模型提案结构已经能解析，但其全局 `confidence` 小于 `MIN_LLM_CONFIDENCE=0.6` 时，系统丢弃整份提案并返回 RuleBased baseline，`fallback_reason` 记录为 `low_confidence`。
- 它不是网络错误、不是动态跨字段校验，也不是 Verifier 的硬约束失败。动态跨字段规则在 TurnInterpreter 的 `Interpretation` 上，要求 `inferred_fields` 同时有原始值、`evidence_map` 和 `extraction_confidence`；缺失时通常是 `invalid_output`/反问。
- 该门槛的安全价值是防止模型在自认不确定时改变骨架；但当前分数是模型自报的单一全局值，阈值尚未校准，且会把可能正确的语义目标一起丢掉。因此它主要是语义召回、任务可用性、延迟和成本风险，而不是硬约束安全漏洞。

[后续建议]

- 先记录 confidence 分布、按 Case/模型的低置信率，以及回退前后语义目标召回和任务成功；不要直接删除检查或盲目降低阈值。
- 若低置信频繁但提案经证据/合同/Verifier 检查仍有效，再考虑把结构置信度与语义置信度拆开，或只保留有证据的语义目标而保留 RuleBased 骨架。该实验应先离线验证，不应在 Pilot 中反向改标签。

### 11.4 下一步优先级（保持 Resume Release 主线）

1. 先用新提交后的干净工作树重跑低成本定向测试和 `compileall`，确认提交没有混入 Key 或评测产物。
2. 保留完整 Pilot 失败明细：区分 `product_failure`、`model_failure`、`fixture_failure`、`runner_failure` 和 `label_review_required`；不要把 fallback 自动算成正常成功。
3. 在同一模型配置下再跑 6–8 条 B1/B2/B3 Pilot，重点观察 PlanningIntent `low_confidence`、TurnInterpreter `invalid_output`、Advisor contract fallback、BGE cold/warm 和 token coverage。
4. 标签已改为 `reviewed`；待 Pilot 失败归因稳定且预算明确后，运行完整 B0–B3 矩阵，并按增量差异归因：B1-B0 看 PlanningIntent，B2-B1 看 Retrieval，B3-B2 看 Advisor。
5. 暂不引入 Agent Loop、MCP、长期记忆、向量数据库或任意骨架重排；先把“模型确实影响语义、Harness 确实能约束和降级、评测能如实解释失败”这条故事闭环。

## 12. 2026-09-14 E2E 标签审核落盘

[代码事实]

- `evals/resume_release_cases.json` 的 35 条 E2E Case 已从 `draft` 更新为 `reviewed`。
- 逐条审核过程摘要、依据和指标范围记录在 [`resume_release_case_review_2026-09-14.md`](resume_release_case_review_2026-09-14.md)。该记录明确说明：摘要是对侧边会话讨论的规范化记录，不冒充逐字 transcript。
- 本轮 35 条均纳入 E2E 主评测分母；`planning/clarification/conflict/modification` 仍按原 category 分组，hard-constraint 分母由 `hard_constraint` 标签确定（当前 7 条）。`reviewed` 只表示预期被人工确认，不表示某个 Variant 必须通过。
- Retrieval holdout 仍为 16 条 reviewed，其中 15 条进入 Recall/nDCG/MRR 分母，1 条仅做 grounding safety；它和 35 条 E2E 不合并为一个成功率。
- `evals/README.md`、数据集测试和本交接文档已同步说明新状态；没有修改 Planner、Graph、数据库或线上业务合同。

[下一步]

1. 先运行数据集/评测 Runner 的低成本测试，确认 `reviewed` Case 不再需要 `--allow-draft`，未来新增 draft 仍受保护。
2. 用同一 qwen3.8 配置完成 6–8 条 B0–B3 Pilot，记录每个失败阶段和调用预算。
3. Pilot 稳定后再跑完整 35 条 E2E 消融；Retrieval 16 条单独重跑。简历数字必须引用报告中的明确 numerator/denominator，并保留模型、数据、索引和 commit 版本。
