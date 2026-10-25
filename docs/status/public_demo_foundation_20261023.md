# 公网 Demo 基础能力交付

Current 分支验证记录，2026-10-23。基于 `main@ba0f7fb` 建立 `codex/public-demo-foundation`；未部署腾讯云，也未验证真实 DeepSeek/高德在线服务。

## 行为变化

- 显式 `HFT_PUBLIC_DEMO=true` 开启单进程公网 gate。页面签发随机访问能力，服务端哈希保存并映射 guest 身份；忽略外部 X-User-Id。会话与模型接口始终要求页面 token；地图 SDK 仅通过 map-only Secure/HttpOnly cookie 获取代理权限。
- 前端 `VITE_PUBLIC_DEMO=true` 时先校验后端模式再启动，每次刷新建立新身份并清除旧 session URL，不在 localStorage/sessionStorage 中恢复访客或 Key。默认本地开发合同保留。
- 左侧栏模型配置固定 DeepSeek 官方 endpoint/model，密码输入框、明确的传输/内存保留说明、配置/移除/失败/等待状态；移动端通过历史侧栏进入。关闭或完成配置时清空输入。公网 HTTP 禁用 Key；后端也拒绝非本机 HTTP 配置。
- 模型配置复用原有三个构建器的显式参数，不修改环境变量、不在 Graph State/RunnableConfig/SQLite 存储客户端。ContextVar 在真实执行线程中选择访客的 Router/Intent/Advisor，SSE 与普通入口一致。无 Key 只运行规则模式，忽略站点 LLM 密钥。公网 turn 禁用继承的 LangSmith hosted tracing。
- 访客/Key 2 小时 TTL、60 秒清理扫描，包含空会话、业务记录及 checkpoints/writes。运行中到期等待 worker 完成；短暂数据库冲突保留待清理项重试。重启回收旧 guest 数据并使旧凭证失效。
- 增加访客数、签发频率、API 请求、新建会话、turn 次数、请求体、执行并发及全站地图/地点调用额度。SSE 断开不提前释放执行锁。
- 四个只读展示会话通过真实离线 HTTP/SQLite 记录，覆盖选择、地点变更 stale/replan、局部替换和预算恢复。“开始体验”只填入自己的新需求，不共享样例数据库，不自动发送。

## 关键入口

- `app/api/public_demo.py`：能力凭证、预算、模型作用域与 ASGI gate。
- `app/api/application.py`、`app/api/server.py`：组合根、配置端点、后台清理与执行线程边界。
- `frontend/src/demoRuntime.ts`、`DemoSettings.tsx`、`DemoShowcases.tsx`：页面级身份与侧栏入口。
- `scripts/build_demo_showcases.py`、`data/demo_showcases/v1.json`：可再录制的离线公开样例。
- 部署接口及精确边界：[公网 Demo](../current/public_demo.md)。未引入 Docker/Nginx 配置，服务器安装由用户当前并行进行。

## 实际验证

- 后端 API/Router/PlanningIntent/Advisor/PublicDemo 回归：116 passed、13 subtests passed；随后补充额度、到期、样例、HTTPS和显式凭据构建检查，公网定向测试最终 12/12 passed（与前述 116 项有重叠，不相加）。没有调用真实付费模型。
- 前端单测：7 文件、102 passed，覆盖配置输入清理、隐私说明、启动失败关闭、临时凭证不持久化及既有业务测试。
- 默认本地模式生产构建通过；`VITE_PUBLIC_DEMO=true` 的公网模式生产构建也通过。
- 默认浏览器回归：36 passed、2 skipped；两项为手机不执行桌面布局合同。
- 公网真实 FastAPI/SQLite 桌面/手机回归：4/4 passed；在最终额度/tracing/案例展示调整后再次确认 4/4 passed。覆盖 Key 配置与移除（仅建立客户端、不发真实模型请求）、跨访客访问、刷新新身份、样例只读与发送前零会话写请求。
- 截图检查桌面与手机共两轮；最终证据：`.impeccable/review/public-demo-settings-{desktop,mobile}.png`、`public-demo-showcase-{desktop,mobile}.png`、`public-demo-fresh-{desktop,mobile}-public.png`。
- 四个录制场景实际状态：普通规划 completed；出发地 context_saved/stale 后 completed；单站替换 completed 且有 PlanDiff；预算 needs_recovery 后 completed。
- `git diff --check` 通过。

## 明确限制

- 尚未上线，不代表腾讯云、TLS/代理头、SSE 代理转发、在线地图或真实 DeepSeek Key 已验证。
- 只支持单 worker/单实例；内存凭证和限额不提供多实例共享、注册登录、持久账户或账单功能。无浏览器关闭即删、磁盘安全擦除、第三方服务不留存等承诺。
- Key 经过本站后端。应用不主动持久化，部署方仍须关闭敏感请求日志/转储，并正确配置 HTTPS、后端入站限制、代理 IP 和上游服务商额度。HTTP 本机调试例外仅用于开发。
- 规则模式不能等价理解任意自然语言；录制案例不代表实时商户信息。Planner 算法与 Frozen 指标没有调整，也没有重跑正式 Frozen/Live 评测来制造新指标。
- 用户此前未跟踪的草稿、patch/zip、独立 ui-polish.css 和旧临时目录保留，不加入本次提交。
