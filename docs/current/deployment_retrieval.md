# BGE 资产与高德配置

_Current，2026-10-25。部署配套实现；本机真实 BGE 已验证，容器/4GB 资源及线上高德尚待验证。与[单机部署指南](deployment.md)配合使用。_

## 先在本机准备资产

使用本机已有 BGE 权重与可选依赖（`requirements-rag.txt`）。仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe deploy/prepare_retrieval.py
tar -czf deploy/retrieval.tar.gz -C deploy retrieval
scp deploy/retrieval.tar.gz root@118.25.83.167:/opt/happyfreetime-retrieval.tar.gz
```

本轮已经生成，当前包约 56.6 MB，无需重复运行准备脚本。脚本仅复制模型/许可白名单，以当前 DemoCatalog 的 198 个候选生成并验证索引，不加载 .env、不联网下载、不打包会话或凭证。`asset_manifest.json` 包含逐文件 SHA-256。输出目录存在时拒绝覆盖；重建可指定新 `--output-dir`，核对后再替换。Catalog/Profile 变化后应重新准备。

模型加载显式使用 `local_files_only=True`、`trust_remote_code=False`，部署同时禁用 Hub 联网，依据 [Sentence Transformers 本地加载参数](https://www.sbert.net/docs/package_reference/sentence_transformer/model.html)。模型文件不进入 Git，Git bundle 只更新代码；服务器无需访问 Hugging Face 获取权重。镜像首次安装 CPU PyTorch、sentence-transformers 等依赖仍需要访问软件源。

服务器检查包内容且确认没有旧资产目录后执行：

```bash
cd /opt/happyfreetime
tar -tzf /opt/happyfreetime-retrieval.tar.gz
tar -xzf /opt/happyfreetime-retrieval.tar.gz -C deploy
```

包内应只有 retrieval/model、index 和资产清单。若已存在 deploy/retrieval，先核对旧版并保留回退资料，不盲目覆盖。

## Docker 与严格就绪检查

后端基础依赖和 CPU BGE 依赖分别在独立阶段安装，业务代码、release 标签及模型资产均在依赖安装之后进入最终镜像。以后仅改源码或 HFT_RELEASE 时可复用依赖层；首次切换到该构建结构仍可能重新安装，不能保证命中旧结构缓存。构建失败时不要替换旧容器，也不要使用 `--no-cache` 或先清理旧镜像。

默认 `HFT_API_TARGET=rag` 使用 CPU-only PyTorch，并将模型、索引及 data/retrieval/v1 语义 Profile 装入镜像；非 root、只读根文件系统和访客隔离保持不变。索引按真实演示目录生成，而不是原始 OSM 的另一套属性。

已有服务器不要覆盖私有配置；同步核对：

```dotenv
# deploy/compose.env
HFT_API_TARGET=rag
# deploy/runtime.env
HFT_CANDIDATE_RETRIEVER_MODE=hybrid
HFT_RETRIEVAL_PREWARM=true
```

lifespan 预热正式 Graph/PlanningService 共用的检索器，先校验模型、Profile 和 Catalog 哈希，再实际编码查询。失败拒绝启动，不能把规则回退标记成就绪。`/api/config/runtime` 的 retrieval 字段返回模式、预热状态、耗时和候选数；同进程新会话复用模型，重启重新预热。健康检查预留 180 秒，不是启动耗时保证。

如需有意回退轻量部署，三项同步改为 `HFT_API_TARGET=runtime`、`HFT_CANDIDATE_RETRIEVER_MODE=rule`、`HFT_RETRIEVAL_PREWARM=false`，重新构建并验收；不能继续宣称启用 BGE。

## 高德完整模式

```bash
cp deploy/amap.env.example deploy/amap.env
chmod 600 deploy/amap.env
```

在服务器私有文件填写 Web 服务 Key、JS Key、安全码。三项齐全后，使用组合配置：

```bash
docker compose --env-file deploy/compose.env -f compose.yaml -f compose.amap.yaml config --quiet
docker compose --env-file deploy/compose.env -f compose.yaml -f compose.amap.yaml build
docker compose --env-file deploy/compose.env -f compose.yaml -f compose.amap.yaml up -d
docker compose --env-file deploy/compose.env -f compose.yaml -f compose.amap.yaml ps
```

该组合启用地图、出发地搜索、路线与天气；不依赖访客 DeepSeek Key，消耗部署者高德额度。JS Key 属于浏览器公开配置，安全码与 Web 服务 Key 留在后端，不进入前端构建变量。既有限流、缓存、来源与失败回退保持不变；地图不把 POI 商业属性/可用性变成实时事实。

后续更新保持同一组 `-f`。增加 HTTPS 时追加 `-f compose.https.yaml`，其余证书操作沿用主部署指南。若只需要地图/地点搜索，仍可只配置 runtime.env 中的对应凭证和 geocoding 模式，不必使用完整 amap 组合。

路线真实 HTTP 调用默认按 `HFT_AMAP_ROUTE_QPS=1` 节流：同一共享 Provider 跨两个并发规划共用预算，前一次 HTTP 完成后再留一个间隔，不积累突发额度；成功缓存和失败缓存命中不再出站，也不等待。设置值必须为有限正数，应依据控制台驾车路线 2.0 的账号 QPS 配额，不能按月调用总量推算。该限制仅约束当前单 worker 的规划出站请求，不协调同账号的其他应用。首次核验会增加等待，后续缓存仍可复用；不增加自动重试、不放宽严格数据检查。

## 验收实际生效，不能只看开关

```bash
docker compose --env-file deploy/compose.env -f compose.yaml -f compose.amap.yaml exec -T api python deploy/smoke.py --base-url http://web:8080 --require-hybrid --require-amap
docker stats --no-stream
```

`--require-hybrid` 不仅看预热标志，还要求一次 SSE 规划具有 BGE 模型调用及 query_count。`--require-amap` 检查地图配置、实际地点搜索、天气与路线来源，不把模拟或降级当成成功；会消耗部署方高德额度。SDK 在线绘制、标记与路线几何仍需浏览器验收。未配置高德时去掉 amap 文件组合和 `--require-amap`，仍可验证 BGE。

此高德门槛检查本轮所有返回方案的天气/路线观测，不是只请求一条路线确认连通性。即使三条方案正常生成，只要某一路段降级，也会严格失败。失败摘要给出天气与路线成功数、失败的方案/路段序号及既有原因码；不输出位置、请求 URL、Key 或原始上游响应。`upstream_failed_using_local_estimate` 只能说明发生回退，不能进一步证明是限流、超时或解析错误。

仅更新检查脚本时，无需重建或修改运行中容器。将新 deploy/smoke.py 单独上传到服务器 `/opt/happyfreetime-smoke-diagnostic.py`，可通过标准输入运行：

```bash
docker compose --env-file deploy/compose.env -f compose.yaml -f compose.amap.yaml exec -T api python - --base-url http://web:8080 --require-hybrid --require-amap < /opt/happyfreetime-smoke-diagnostic.py
```

这次检查仍会实际规划、消耗服务额度，不能反复运行直到偶然通过就宣布间歇问题已修复。

若摘要只有通用降级原因，可使用 `deploy/route-diagnostics.compose.yaml` 临时只读覆盖两个 Provider 源文件，采集实际失败处的 `amap_route_failure` 日志。先把本机 app/providers/route.py、amap.py 与该 overlay 上传至服务器 `/opt/happyfreetime-route-diagnostics/`，并确保源文件可读；在原 Compose 组合后追加该目录中的 overlay，执行 `up -d --no-build --wait api`。镜像、模型及凭证无需重建，但 API 会重启并重新预热，页面需刷新获得新临时身份。

复现一次后仅取 `logs --since 5m api` 中含 `amap_route_failure` 的行：输出固定错误类别、数字码与耗时，不应含 URL、Key、位置或原始上游内容。没有这组证据前不调整限流、超时或解析逻辑。诊断完成后，移除 overlay、用原文件组合再次 `up -d --no-build api` 恢复原镜像代码；正式修复仍需正常发布版本。

本次日志已确认 10021。若正在使用诊断挂载，上传更新的 route.py 后，必须 `up -d --no-build --force-recreate --wait api`，文件内容变化不会自动触发 Compose 重建容器。此时补丁通过覆盖文件生效，原业务镜像版本没有改变；之后需把补丁纳入正式镜像，再移除临时覆盖。耗时日志包含本地节流等待，不能把 elapsed_ms 全部当作上游网络时间。

记录 api 内存/CPU、重启次数、预热耗时及连续两轮规划。当前内存上限由 HFT_API_MEMORY_LIMIT 控制；本机成功不证明 4GB 服务器容量足够，OOM/超时不能算通过。保留旧镜像，不使用 down -v 删除数据。

## 本地预览

```powershell
.\scripts\start_local.ps1 -HybridRetrieval
.\scripts\start_local.ps1 -HybridRetrieval -LiveAmap
```

第一条只用本地 BGE 与模拟 Provider；第二条显式从本机 .env/环境读取三项高德凭证，启用地图/搜索/路线/天气，仍屏蔽站主 LLM。`-WithAmap` 保持原来仅地图与可选搜索的含义。默认本地启动仍为轻量 rule；`-Check` 使用独立临时数据库，启动、预热、就绪后自动退出，不清理日常本地体验库。
