# 公网 Demo 部署配置核对

_Current 状态记录：2026-10-23；不是云端验收或 Frozen 指标。_

## 交付范围

沿用 `codex/public-demo-foundation`，依赖公网基础提交 `ddd867a`，没有重新从 main 建立而遗漏前序实现。用户服务器反馈：OpenCloudOS 命令环境、4 核 / 4GB / 40GB，Docker 29.9.2、Compose v5.6.1、Docker 服务 active；尚无本会话 SSH 访问或云端操作。

新增两个 Dockerfile、构建上下文白名单、Compose 单进程拓扑、Nginx SSE 反向代理及 HTTPS 叠加配置、环境模板、可选停机数据卷快照和部署后冒烟脚本。默认 loopback 监听，API 不映射宿主端口；两个非 root 容器只读根目录，SQLite 使用独立卷。Nginx 覆盖客户端转发头，后端仅信任指定内部代理 IP，限制体积/请求/连接并控制日志。部署者 LLM Key 强制为空。

`/api/config/runtime` 增加 release 字段，与镜像构建写入的前端 version.json 对照。冒烟检查页面、版本、公网开关、未认证拒绝、跨访客隔离、案例和 SSE 规划，删除自建会话，不接收 Key。不增加登录、收费预算系统、自动部署或证书自动续期。

README、CODEX_BRIEF、公网边界与部署指南同步；当前主链和 Planner 合同未改变。

## 实际验证

- `.venv/Scripts/python.exe -m pytest tests/test_deploy.py tests/test_public_demo.py -q`：18 passed。
- 部署测试覆盖配置静态合同、代理头与日志策略、镜像上下文约束、SQLite/sidecar 快照，以及通过 TestClient 运行真实 FastAPI/SQLite/SSE 的冒烟脚本。静态页面使用测试替身，没有真实 Nginx。
- 首次部署冒烟单测发现测试把静态页面错误交给 API 身份门禁，返回 401；修正测试以模拟 Nginx 的静态资源边界后通过，无需放宽 API 门禁。
- 当前本机没有 Docker CLI/daemon，因此没有执行 `docker compose config`、镜像构建、容器健康、`nginx -t` 或 HTTPS 验证。Python/YAML 测试不能替代这些门槛。
- 前端业务源代码没有变化，不重复上一提交已运行的前端和浏览器全套测试；真实 DeepSeek Key、在线高德仍未验证。

## 发布门槛

2026-10-24 服务器首次构建反馈：三个基础镜像和 npm 依赖下载成功，但前端安装因 `ERR_PNPM_IGNORED_BUILDS` 失败。根因是 Dockerfile 与构建上下文白名单遗漏已有的 `frontend/pnpm-workspace.yaml`，没有带入仅允许 esbuild 的 `allowBuilds` 策略。现补齐该文件的传入并增加配置断言；不升级 pnpm、不放开全部依赖脚本。后端安装显示 CANCELED 是并行前端失败后的取消，不能据此判断后端安装成功或失败。修正后仍需服务器重新构建验证。

可以提交为待服务器验证的 PR；不能宣称公网部署验收通过。服务器先完成默认私有启动、Nginx 配置检查、冒烟、浏览器操作，再确认域名与 HTTPS 并做公网复验。首次 Release 应在这些检查后标记；本记录不授权自动合并或发布。

独立数据卷不改变临时访客保留策略；备份可包含会话和位置，默认不自动长期保存。禁止直接将本地开发模式开放公网。真实站点 Provider 的调用成本需要服务商额度控制；当前没有完整跨 IP/分布式商业预算系统。
