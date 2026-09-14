# 2025–2026 Agent 应用开发 / Applied AI / AI Agent Engineer 岗位调研

调研日期：2026-09-06。来源限定为公司官方招聘页或官方工程资料；岗位页面会动态更新，以下记录的是页面当前可见的 2025–2026 招聘信号。百度岗位对中国候选人尤其有参考价值。

## 最值得写进项目描述的 8 个信号

1. **端到端生产落地**：写清从需求/场景选择、架构、原型、评测到上线、规模化和实际采用，而不是只写“做过 Demo”。
2. **Agent Harness / 可控执行底座**：状态管理、沙箱、上下文交接/压缩、执行约束、权限与人类审批、长任务稳定性。
3. **工具调用与任务闭环**：任务拆解/规划、工具/API 调用、环境反馈、重试/反思、记忆或多 Agent 协作；最好给出复杂任务完成率。
4. **评测驱动迭代**：有代表性数据集、任务级/轨迹级评测、失败 case 分析、回归 benchmark，并量化成功率、质量、延迟和成本。
5. **可观测性与反馈飞轮**：保存 traces/runs，能从线上失败轨迹生成离线 eval，用用户/人工反馈驱动 prompt、工具、上下文或模型改进。
6. **可靠性、性能与成本意识**：明确延迟、吞吐、token/调用成本、可用性、错误恢复和安全/治理边界。
7. **业务理解与系统集成**：把业务目标转成 AI 方案，接入企业系统、知识库/RAG、工作流/API，并说明用户采用或业务指标。
8. **扎实工程与协作证据**：Python/Go/Java 等工程能力、测试/部署/云原生或 CI/CD；开源贡献、论文/竞赛可作为加分项，但应放在真实交付之后。

## 一、岗位明确要求（招聘方原文信号）

### 中国岗位：百度官方校园招聘

- **Agent Harness 研发工程师（2026-07-21）**明确要求建设“可读、可控、可验证”的 Agent 支撑体系，覆盖状态管理、沙箱环境、反馈闭环、执行约束、上下文交接和 compaction，保障长时间高复杂度执行；同时要求理解 ReAct/CoT/Plan-and-Execute，熟悉 PyTorch、LangChain、AutoGen，并具备从算法设计到工程落地的完整能力。岗位还强调复杂文件读写、环境反馈、长任务执行中的框架分析，以及开源贡献/早期产品搭建/Harness 落地等加分项。[百度官方岗位：Agent Harness 研发工程师](https://talent.baidu.com/jobs/detail/GRADUATE/8683fc23-9bff-4d49-94b2-f5ddce782c5a)
- **智能体算法工程师（2026-07-21）**要求全链路 Agent 设计研发，涉及自主规划、复杂推理、动态决策、意图理解、多步规划、工具调用、长短期记忆、多 Agent 协同；并要求建立“效果、效率、资源成本”一体化评估体系，推动从技术验证到业务产品落地。岗位要求熟悉 Agent 原理、PyTorch、LangChain、AutoGen；产品早期搭建、顶会论文和知名开源贡献是加分项。[百度官方岗位：智能体算法工程师](https://talent.baidu.com/jobs/detail/GRADUATE/02f73086-be71-4d09-8d6e-f1c6981b8b48)
- **AI 应用工程师（2026-07-21）**强调企业 IT/安全/工程场景的 Agent 产品规划、方案设计、开发迭代与落地；要求 Agent、知识库、工作流建设，API 对接、流程编排、自动化方案设计，以及把业务需求转成 AI 方案并持续跟踪效果。[百度官方岗位：AI 应用工程师](https://talent.baidu.com/jobs/detail/SOCIAL/b286456b-922b-4ff3-9da0-0e8855443ba0)
- **NLP/Agent 算法工程师（2026-07-21）**要求办公 Agent 框架/核心算法、LLM 微调优化、分析产品数据并建立数据飞轮；技能侧明确 Python/Go/Shell、PyTorch/TensorFlow/Paddle，以及调试和优化落地能力。[百度官方岗位：NLP/Agent 算法工程师](https://talent.baidu.com/jobs/detail/SOCIAL/5bb42582-10ab-4f49-94a6-7ee296885d8f)

### 国际头部岗位：OpenAI 官方招聘

- **Applied AI Engineer, Digital Natives**要求亲自带领复杂生产级 AI/ML 系统，从模糊发现走到生产部署、用户采用和可量化业务影响；工作内容包括代码编写与调试、评估系统、复杂集成，并权衡模型行为、可靠性、延迟、成本、安全、治理和运维准备度。[OpenAI 官方岗位](https://openai.com/careers/applied-ai-engineer-digital-natives-san-francisco/)
- **Applied AI Engineer, Codex Core Agent**的团队描述把关注点放在真实软件工程任务上的 Agent 性能、token/延迟/可靠性/成本/容量生产边界、执行循环与接口、基础设施，以及把真实使用反馈转化为模型和 Agent 行为改进。[OpenAI 官方岗位](https://openai.com/careers/applied-ai-engineer-codex-core-agent-san-francisco/)

## 二、从官方工程实践推断的面试关注点（明确标为推断）

以下不是招聘方逐字承诺，而是根据官方工程资料中反复出现的生产实践，推断面试官可能要求候选人展示的证据：

1. **会用评测证明改进，而非凭 Demo 主观判断。** OpenAI 的 Agent 工具发布将 Agents SDK、工具调用与 tracing/observability 放在同一套生产构建基础上；LangChain 官方资料把 structured-output、tool-call 和 trajectory evaluation 作为 Agent 评测重点。因此项目描述最好给出数据集、指标、基线、失败样例和版本前后对比。[OpenAI：New tools for building agents](https://openai.com/index/new-tools-for-building-agents/)；[LangChain：OpenEvals](https://www.langchain.com/blog/evaluating-llms-with-openevals)
2. **会解释一条 Agent 轨迹为什么成功或失败。** LangChain 将 run/trace/thread 作为观察和评测粒度，强调线上轨迹应反哺离线数据集；这推断出面试可能追问 trace schema、错误归因和回归测试如何落地。[LangChain：Agent observability powers agent evaluation](https://www.langchain.com/blog/agent-observability-powers-agent-evaluation)
3. **会做“最小可行、可演进”的架构取舍。** Anthropic 官方架构资料建议先从单一用途 Agent 开始，以较低成本和更清晰指标验证 ROI，再按数据演进为更复杂的多 Agent；面试中可能考察为何选择单 Agent、何时拆分，以及如何控制复杂度。[Anthropic：Building Effective AI Agents](https://resources.anthropic.com/hubfs/Building%20Effective%20AI%20Agents-%20Architecture%20Patterns%20and%20Implementation%20Frameworks.pdf)
4. **会把安全与人类控制作为系统设计的一部分。** Anthropic 将 harness、tools、environment 与模型并列为 Agent 行为层，并强调权限、审批、透明度、隐私和 prompt injection 防护；因此“工具权限、不可逆操作确认、沙箱/隔离、审计日志”很可能成为系统设计追问。[Anthropic：Trustworthy agents in practice](https://www.anthropic.com/research/trustworthy-agents)
5. **会把线上运营指标纳入产品定义。** OpenAI 岗位把生产采用、可靠性、延迟、成本、安全与治理列为交付判断；据此推断，仅报告准确率不足，还应说明 p95 延迟、成本、失败恢复、可用性和真实用户采用。[OpenAI：Applied AI Engineer, Digital Natives](https://openai.com/careers/applied-ai-engineer-digital-natives-san-francisco/)

## 三、简历改写建议

项目 bullet 尽量采用：**业务场景 + Agent 行为/工具 + 工程底座 + 评测/观测方法 + 量化结果 + 规模/约束**。例如不要只写“基于 LangChain 搭建问答 Agent”，而写“为 X 场景构建带检索与工具调用的 Agent；加入权限/沙箱、轨迹 tracing 和任务级 eval；在 N 条真实任务上将成功率从 A 提升到 B，p95 延迟/单任务成本为 C/D，并上线服务 Y 名用户”。

注意：框架名称本身只是入口信号；上述岗位共同强调的是可验证的生产交付、可靠性/成本/安全和持续迭代证据。
