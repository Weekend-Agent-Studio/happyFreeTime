# 公网 Demo 私有试运行与展示修正

_Current 状态记录，2026-10-24。不是公网发布、真实模型验收或 Frozen 评测。_

## 服务器观察

用户提供的服务器记录：`1531293` 镜像构建后 api healthy、web started。主会话通过用户 SSH 隧道 `127.0.0.1:18080`，实际操作无 Key 规划、详情与证据、正式选择和单站替换。生成 3 个初始候选和 2 个替换候选及 PlanDiff。补看 1440×900 桌面双栏，未遇到该路径的阻断。没有 SSH 接管、没有真实 Key、没有公网 HTTPS 验证；未取得用户服务器 nginx -t 和 smoke 脚本结果，不推断通过。

## 本轮实现

- 天气摘要直接显示来源。模拟/回放模式优先于 source=cache，避免缓存掩盖数据模式。
- 营业与可用性概览列出事实来源；未验证、过期、降级或 unknown 显示待确认。不再把模拟可用性概括成“已提供动态事实”。证据中的模拟标识改为“模拟数据 · 非实时”。
- 默认换站快捷操作改为“换一个地点”，不改后端替换 DTO，不承诺原来未实现的同类别约束。
- 原始 OSM 快照将天福茗茶和北京明城墙遗址公园都分类为 museum；前者同时使用茶馆参考图，后者名称为公园。没有足够证据确认新的类别，本次将两条明确存疑记录暂时移出 DemoCatalog 召回，保留原始 200 条快照和确定性 Demo World 产物；最多 198 条候选。不是按名称猜测或改写商户事实，也不修改 Frozen 数据报告。
- 增加模拟/回放/未验证实时事实的概览测试、目录隔离测试，以及真实 FastAPI/SQLite 桌面浏览器换站验证。

## 实际验证

- 后端：`pytest tests/test_demo_world.py tests/test_public_demo.py tests/test_plan_modification.py -q`，36 passed。
- 前端：`vitest run --maxWorkers=1 --testTimeout=15000`，7 文件、105 passed。首次沙箱内 worker 启动超时；允许本机子进程后并行运行 101 passed、4 项超过默认 5 秒，没有断言失败；最终串行按每项 15 秒复验全部通过。没有修改测试配置文件或业务代码来绕过这些超时。
- `pnpm --dir frontend build`：类型检查和生产构建通过。
- 桌面真实 API/SQLite：`playwright test --config playwright.public-demo.config.ts --project desktop-public --grep "desktop demo labels"`，1 passed。覆盖模拟天气摘要、未展开证据前的可用性来源、正式选择、改名后的换站操作及 PlanDiff。没有调用真实模型。
- 检查截图：`.impeccable/review/public-demo-polish-evidence-desktop.png`、`public-demo-polish-replacement-desktop.png`；均为本机真实测试服务，已经视觉检查，不冒充腾讯云新镜像截图。
- `git diff --check` 通过。没有本机 Docker，不重复旧 Frozen/Live 指标，也不将定向结果称为公网或全仓验收。

## 范围与后续

本轮不实现案例续聊、下单、固定多轮案例入侧栏或下一步提示按钮。真实 Key 测试由用户在页面自行配置，配置成功不代表已调用模型。前述案例和 README 产品包装作为后续独立工作，不将讨论写成已交付能力。服务器仍运行旧镜像，需传入本轮提交、更新 HFT_RELEASE 并重新构建/启动才能看到修正。
