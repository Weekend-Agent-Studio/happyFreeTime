# 单机公网 Demo 部署

_Current：2026-10-23。部署文件已提供；本机未安装 Docker，镜像构建、腾讯云容器运行、证书与公网访问尚待服务器验证。不是已上线报告。_

## 先理解这条链路

```text
GitHub 代码 → Dockerfile 构建两个镜像 → Compose 启动两个容器
浏览器 → web 容器中的 Nginx → /api/* 转发给 api 容器中的 FastAPI
                            → 其他路径返回 React 构建后的页面
api 容器 → 独立 SQLite 数据卷 happyfreetime-data
```

镜像是打包好的运行模板，容器是模板启动后的进程。Compose 负责一起启动服务、连接内部网络和挂载数据；Nginx 已装在 web 镜像里，服务器无需再安装一套 Nginx、Node 或 Python。Dockerfile 不会自动上线，GitHub PR 合并也不会自动更新服务器。

GitHub Release 是基于 Git tag 的版本说明和附件页面，适合标记一次可回退版本；它不是镜像仓库，也不等于部署。本仓库当前没有自动部署流水线。建议先完成服务器冒烟，再创建首个 Demo 预发布版本。

当前配置面向单机、单实例、单 worker。4 核 / 4GB / 40GB 可作为首次试运行环境，不加载 BGE 或本地 LLM；并非压测容量承诺。初次下载依赖和构建比日常运行更耗时、耗磁盘。

## 1. 服务器获取代码

以下命令在 Linux 服务器运行。若没有 Git，先通过系统包管理器安装（OpenCloudOS 可用 `dnf install -y git`）。目前部署文件位于开发分支，PR 合并后改为使用 main 或经过验证的 tag。

```bash
mkdir -p /opt
cd /opt
git clone --branch codex/public-demo-foundation https://github.com/Weekend-Agent-Studio/happyFreeTime.git happyfreetime
cd /opt/happyfreetime
cp deploy/compose.env.example deploy/compose.env
cp deploy/runtime.env.example deploy/runtime.env
chmod 600 deploy/compose.env deploy/runtime.env
sed -i "s/^HFT_RELEASE=.*/HFT_RELEASE=$(git rev-parse --short HEAD)/" deploy/compose.env
```

若目录已经存在，先检查它的分支与本地改动，不要覆盖。未推送分支时以上 clone 无法取得它，需要先完成推送。

两份配置用途不同：`compose.env` 设置镜像版本、端口和 Docker 网络；`runtime.env` 设置后端行为。它们、证书、维护快照均被 Git 和镜像构建上下文排除。不要把 Key 写进 Dockerfile 或 `VITE_*`。

首次保留示例默认值：只监听服务器 `127.0.0.1:8080`；模型为规则降级，天气、路线与地点为模拟数据，没有部署者共享 LLM Key。访客后续可经 HTTPS 临时配置自己的 DeepSeek Key。真实 POI 商业数据和线上地图效果没有因为部署而自动补齐。

## 2. 构建并启动，先不开放公网

```bash
docker compose --env-file deploy/compose.env config --quiet
docker compose --env-file deploy/compose.env build
docker compose --env-file deploy/compose.env up -d
docker compose --env-file deploy/compose.env ps
docker compose --env-file deploy/compose.env logs --tail 100
```

不要在外部聊天中粘贴包含秘密的完整配置输出；`config --quiet` 只验证配置。构建时需要访问镜像源、PyPI 和 npm，下载失败应按报错检查网络，不应换成来源不明的安装脚本。

api 健康后 web 才启动；首次可能要等待。若报 Docker 子网重叠，在 `compose.env` 中将 `HFT_NETWORK_SUBNET`、`HFT_PROXY_IP`、`HFT_API_IP` 一起改成未使用的同一子网。不要为了腾出地址删除其他项目的网络。

检查实际容器与反向代理：

```bash
docker compose --env-file deploy/compose.env exec -T web nginx -t
docker compose --env-file deploy/compose.env exec -T api python deploy/smoke.py --base-url http://web:8080 --plan
```

脚本检查页面、前后端版本一致、公网开关、无凭证拒绝、两个访客隔离、三条录制示例和一次 SSE 规划，并删除自己创建的会话。它不接收 API Key。默认 Provider 全为模拟，不产生模型费用；若日后修改 Provider 配置，此检查会使用修改后的站点服务。它不代替桌面/手机浏览器与真实 LLM 联调。

## 3. 从自己的电脑看页面

在你自己的电脑另开终端，保持下面连接运行：

```bash
ssh -N -L 8080:127.0.0.1:8080 root@118.25.83.167
```

然后电脑浏览器访问 `http://127.0.0.1:8080`。这里通过 SSH 加密隧道连接服务器上的私有服务；不是把电脑当服务器。若电脑的 8080 已占用，左边改为 18080，浏览器也访问 18080。

先检查案例、创建会话、规划、修改条件、选择和换站；刷新后不恢复会话。此阶段保持 Key 为空。无需开放 8000、5173 或公网 8080；SSH 入口应尽量限制为自己的来源 IP。

## 4. 配置域名与 HTTPS 后，才开放访客

这一步需要确认服务器地域、域名及域名解析/证书条件，目前尚未完成。不要直接将纯 HTTP 页面开放给访客填写 Key。

提供 `compose.https.yaml` 与 Nginx TLS 模板；证书申请和自动续期暂未自动化。将有效域名证书和私钥放入以下位置（不要提交 Git）：

```text
deploy/certs/fullchain.pem
deploy/certs/privkey.pem
```

容器 Nginx 以 UID/GID 101 运行，需能读取证书：

```bash
chown -R root:101 deploy/certs
chmod 750 deploy/certs
chmod 640 deploy/certs/fullchain.pem deploy/certs/privkey.pem
```

在 `deploy/compose.env` 改为 `HFT_BIND_ADDRESS=0.0.0.0`、`HFT_HTTP_PORT=80`、`HFT_HTTPS_PORT=443`，云防火墙按需放行 80/443，然后使用两份 Compose 文件：

```bash
docker compose --env-file deploy/compose.env -f compose.yaml -f compose.https.yaml run --rm --no-deps web nginx -t
docker compose --env-file deploy/compose.env -f compose.yaml -f compose.https.yaml up -d
```

前一命令要求已启动内部 api，且证书可读；检查失败先修复，不执行后一命令。HTTP 会转向 HTTPS。之后所有创建/更新 web 的命令都带上 HTTPS 文件，避免退回 HTTP 配置。云防火墙、系统防火墙、端口占用或 SELinux 拒绝挂载，应针对日志排查，不要直接关闭整套安全机制。

从外部浏览器检查证书有效、HTTP 跳转、桌面/手机交互、刷新隔离，再用实际域名运行 `deploy/smoke.py`。如服务器无法回访自身公网地址，可从有 Python 的外部电脑运行脚本。真实 DeepSeek Key 和在线高德需要另外做小额度联调；未通过前不能写成“已验证”。

证书续期后先 `exec web nginx -t` 再 `exec web nginx -s reload`（带相同 Compose 参数），并核实浏览器证书到期时间。文件必须保持容器可读。

## 5. 数据、更新与回退

`happyfreetime-data` 保存 SQLite 与 checkpoint，代码和容器更新不删除卷。公网 Demo 的访客身份和模型 Key 在内存，Key 不写入该卷；刷新不恢复历史不表示数据库立刻擦除。访客业务数据按期限清理，进程重启清理失去身份的访客数据，详见 [公网隐私和期限边界](public_demo.md)。这不是长期用户历史服务。

需要维护快照时可运行 `bash deploy/backup.sh`：短暂停止 web/api，使用当前 api 镜像离线打包整个卷（含 SQLite sidecar），随后恢复原容器。它会造成短暂中断，并丢失内存中的访客身份。默认不自动备份访客聊天；快照可能含临时会话和位置，保持私有、建议 24 小时内删除。Key 不在数据库不代表所有聊天内容都不敏感。还原快照也不会恢复临时访客身份。

日常更新顺序：

1. 记录当前 Git SHA、`compose.env` 的版本和运行镜像；保留已验证的旧镜像。
2. 获取并切换新代码，检查本地配置没有被覆盖；`HFT_RELEASE` 改为新 Git SHA。
3. 先 `build`；构建失败时旧容器仍可运行。
4. 按需做短期维护快照，再 `up -d`（公网使用两份 Compose 文件）。
5. 运行冒烟和浏览器检查，确认后记录发布版本。

回退到已有本地旧镜像时，切回对应旧代码、恢复旧 `HFT_RELEASE`，使用相同 Compose 文件执行 `up -d --no-build`，再冒烟。只切 Git 不会替换运行中的容器。基础镜像和部分 Python 依赖使用版本范围，重新构建旧代码不保证得到完全相同镜像，因此保留旧镜像更可靠。未来若修改数据库结构，必须额外制定数据回退方案；不能只回退程序。

不要把 `down -v` 或 `docker system prune --volumes` 当更新操作。40GB 磁盘需定期查看 `docker system df`、日志和短期快照占用，确认不再需要后再删除旧镜像。

## 已提供的防护与剩余边界

默认无站点付费服务、无部署者共享模型 Key；访客 capability 和模型配置只存内存。应用限制访客签发、会话、请求、turn、并发和输出；Nginx 增加 IP 请求/连接和请求体限制，覆盖客户端转发头，只信任内部 Nginx IP，SSE 禁止缓冲和失败重放。日志不记录正文、查询串、Key 或 Cookie。容器非 root、只读根文件系统、限制内存/CPU/日志及 core dump。

这些措施降低滥用风险，不保证无法攻击或抵御分布式流量攻击。不要增加多个 worker/副本；进程内限流与身份无法跨进程共享。内部真实路线/天气调用没有完整商业预算系统，启用站点高德等服务前需在服务商设置额度。当前不承诺登录、长期历史、真实预订、多机高可用或自动部署。
