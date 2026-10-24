# 公网 Demo：体验边界与部署接入

Current，2026-10-24，`codex/public-demo-foundation` 分支实现；用户腾讯云已运行 `1531293` 并通过 SSH 隧道完成部分界面体验，尚未完成公网 HTTPS 或真实 DeepSeek 联调。本文不替代 HTTPS、反向代理、云防火墙等部署配置。

## 体验模式

- 未配置 Key：现有 DemoRouter、规则 PlanningIntent/Advisor 运行真实确定性规划链；理解范围有限，推荐先从预置需求开始。不是完整自然语言能力的等价替代。
- 展示天气及可用性时直接标明模拟、回放或实际来源，不将模拟缓存显示为实时事实；“换一个地点”只承诺按当前行程角色替换，不承诺同一地点类别。DemoCatalog 暂停召回原始分类存疑的天福茗茶与北京明城墙遗址公园；原始 OSM 快照不据名称擅自改写，核实前不参与新方案。
- 自配 DeepSeek：左侧栏“模型配置”，移动端从历史侧栏进入。固定官方 HTTPS endpoint 和 `deepseek-flash`，不支持任意 base URL 或模型。仅增强 Router、PlanningIntent、Advisor，不改变硬约束校验、数据真实性或地图模式。
- 配置成功只代表服务端接受并建立客户端，`validated=false`；没有为了“验证 Key”额外发送付费请求。无效 Key、额度不足、网络错误沿用现有有界错误分类与规则回退，不伪装为模型成功。
- 左侧“示例会话”固定三条多轮记录，与“本次体验”分组：补充条件再选择、单站替换、预算冲突与恢复。由真实离线 HTTP/SQLite 链录制，每轮保留原始 AgentResponse，复用正常聊天卡片、PlanDiff、行程/地图回退/POI 与核验详情。不是实时生成、真实商户事实或真实 LLM 录制。示例不可修改条件、选择/换站、回答反问或提交恢复；“用此需求新建体验”只清空视图并填入初始需求草稿，发送前零会话写请求。刷新仍显示三条示例，不恢复个人历史。
- 当前有效方案回复下提供“接下来可以做”：查看核验依据、调整预算；正式选定且预览当前已选方案后，才提供换站入口。有明确 dinner 角色时显示“晚饭换安静一点”，否则按实际站点显示“换一家餐厅”或“更换其中一站”。点击只进入详情、条件编辑或替换草稿；保存/发送仍需用户明确操作。历史预览、示例和待恢复状态不会提供可执行的新快捷请求，没有下单入口。

## 临时身份与数据

公网模式下，页面加载先读取 runtime config，再 POST `/api/demo/visitors` 获得随机 capability。服务端只保存 token 的 SHA-256 摘要和临时主体，不信任客户端 `X-User-Id`；所有会话、条件、选择、消息、地点搜索都经过同一 ASGI gate。capability 只保存在当前 JS 模块内存中，刷新时重新签发并移除旧 session URL。

访客不需要注册登录，但仍有服务端访问隔离。旧 token 在到期前仍可能有效；“刷新不恢复”不代表立即吊销旧 token。示例回看不会创建共享用户或公共可编辑历史。

访客和模型配置最长 2 小时。每 60 秒扫描到期主体，删除该主体的业务数据、空会话、checkpoint 和 writes；SQLite 短暂冲突会保留待清理项并重试。正在执行的工作不会在中途删除，完成后下一次扫描清理。停止服务期间无法定时清理，下次公网启动会回收所有旧 `guest-` 主体数据；服务重启后旧凭证失效。SQL 删除不等于磁盘取证级安全擦除，运行数据库不应进入长期备份或发布镜像。

模型 Key 不进入 RequestPatch、Graph State/RunnableConfig、checkpoint、业务数据库、浏览器 localStorage/sessionStorage 或 URL；只存在于配置请求及有期限的后端客户端内存。输入框关闭、成功或失败后清空。显式移除会释放访客模型引用，后续使用规则模式。公网 turn 禁用继承自开发环境的 LangSmith hosted tracing。HTTP access log 只记录路径/状态，部署方不得另外启用请求体、敏感请求头、SDK debug 日志或包含进程内存的转储。

本站后端会接收 Key，并将规划所需内容发送给 DeepSeek，因此不能承诺“Key 不经过本站”或“没有第三方处理”。不声称能控制用户自行安装的密码管理器。公网 HTTP 页面禁用 Key 输入；服务端对非本机 HTTP 配置请求也拒绝。TLS 终止后必须通过受信任代理传递 scheme，不能向公网开放绕过代理的后端端口。

地图 SDK 使用仅限 `/_AMapService/` 路径的 Secure、HttpOnly、SameSite=Strict 临时 cookie，以兼容 SDK 无法附带自定义 Header 的请求。该 cookie 不能授权会话/模型 API。浏览器标签页可能共享此 map-only cookie，session capability 仍是各页面独立；地图额度与业务状态不绑定为同一用户身份。

## 默认资源预算

| 项目 | 默认上限 |
| --- | --- |
| 有效/待到期访客 | 单进程 100 |
| 签发访问凭证 | 每来源 IP 每分钟 10 次 |
| 访客 API 请求 | 每分钟 90 次 |
| 地点搜索与地图代理 | 全站合计每分钟 120 次 |
| 新建会话 | 每次体验 12 个 |
| 消息、条件更新、重新规划 | 每次体验合计 30 次 |
| 执行中的 turn | 每访客 1 个、全站 2 个 |
| HTTP 请求体 | 16 KiB，含 chunked body |
| 单次模型调用 | 15 秒、SDK 自动重试 0、最大输出 2048 token；现有格式修复次数仍有界 |

已运行任务在 SSE 断开后可能继续完成，额度和 busy 锁按后台 worker 真正结束释放，不以浏览器断连作为终止证明。此方案面向单机 Demo，不能作为付费服务的计费系统或分布式限流。跨 IP 滥用仍需部署层限流；地图服务应额外在服务商处设额度。代理 IP 必须正确配置，否则多个访客可能共享代理 IP 的签发预算。

## 如何与服务器环境衔接

仓库现在提供 Docker/Compose、Nginx 和 HTTPS 模板，逐步操作见[单机部署指南](deployment.md)。默认仅服务器本机访问，待镜像构建与容器验证；不能将文件存在等同于已上线。

后端运行时：`HFT_PUBLIC_DEMO=true`。前端构建时：`VITE_PUBLIC_DEMO=true`。两者是不同开关；前端配置不一致会在启动时停下，不能静默退到共享 demo-user。

Linux 本机验证命令示例（不是完整的公网发布命令）：

```bash
HFT_PUBLIC_DEMO=true HFT_DATABASE_PATH=data/public_demo.db \
  HFT_CANDIDATE_RETRIEVER_MODE=rule \
  python -m uvicorn app.api.server:app --host 127.0.0.1 --port 8000 --workers 1

# 另一终端，仅用于本机开发；正式前端由 Nginx 返回构建产物。
VITE_PUBLIC_DEMO=true pnpm --dir frontend dev

# 发布时必须在构建阶段设置，不能事后向已经构建好的网页注入：
VITE_PUBLIC_DEMO=true pnpm --dir frontend build
```

Docker/Compose 配置需保持 **一个应用进程、一个 Uvicorn worker、一个实例**。不要启用多 worker、多个副本或以进程内状态做集群。SQLite 与代码分离挂载，公开演示数据与开发数据库分开；依赖前台 HTTPS、Nginx SSE 关闭缓冲、超时和请求体限制、受限后端网络。设置容器/日志轮转的内存和磁盘上限，关闭进程 core dump。正式部署检查 `/api/config/runtime` 确实返回 `public_demo=true`，且不带凭证的 `/api/sessions` 返回 401。

`HFT_PUBLIC_DEMO=false` 是既有本地开发合同，保留固定 demo-user 和环境变量模型配置，不能将这个模式直接开放公网。部署方的高德 Key 仍由后端配置，与访客 DeepSeek Key 独立。不要把真实 Key 写入 `VITE_*`。

## 复验

```bash
python -m pytest tests/test_public_demo.py -q
pnpm --dir frontend test
pnpm --dir frontend build
pnpm --dir frontend test:public-demo
```

展示内容更新：`python scripts/build_demo_showcases.py`。脚本使用临时 SQLite、固定模拟日期和离线 Provider，不加载 .env，也不复制真实用户历史；默认会覆盖预置 JSON，更新时应审阅差异。
