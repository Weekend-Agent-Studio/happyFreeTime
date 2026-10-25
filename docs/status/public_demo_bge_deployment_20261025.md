# 公网 Demo BGE 打包、预热与高德完整配置

_Current 状态记录，2026-10-25，依赖 0d1af9b，沿用 codex/public-demo-foundation；不是新服务器上线或冻结指标报告。_

## 实现

- 模型和索引从 Git 外单独准备。prepare_retrieval.py 仅复制模型/许可白名单，以当前 DemoCatalog（198 条）构建索引，实际编码验证后输出文件哈希清单；不读取 .env、不下载权重、不复制用户数据。输出目录存在时拒绝覆盖。
- DemoCatalog 增加与 SnapshotCatalog 一致的防御性候选导出，避免用原始 OSM 属性为演示目录建另一套索引。
- Docker 多阶段区分 runtime 与 rag；默认 Compose 选 rag，安装 CPU-only PyTorch/可选依赖，复制权重、索引和先前缺失的语义 Profile。运行时显式只加载本地文件，不接受远程模型代码；不在首个访客请求时下载权重。
- Hybrid.warmup 校验 Profile 与当前 Catalog 的 assembled hash，并实际查询；lifespan 在 Graph 使用的同一检索器上预热。失败关闭资源、拒绝启动，不伪装成规则成功；成功后 runtime 暴露安全的模式/候选数/耗时。运行中原有有界回退语义保持不变。
- 本地 -HybridRetrieval 使用准备资产并预热；-LiveAmap 显式启用三项高德凭证对应的地图/地点搜索/路线/天气，不继承站主 LLM。原 -WithAmap 地图/搜索开关保留。-Check 使用独立临时数据库。
- 生产新增 compose.amap.yaml 与私有 amap.env 模板；模型 Key 的访客隔离与限流没有改变。POI 商业属性/可用性仍模拟，不因高德接入而变成实时事实。
- smoke.py --require-hybrid 验证预热且要求 SSE 中实际编码调用；--require-amap 检查地图配置、真实搜索、天气/路线来源，模拟或降级不通过。地图 SDK 绘制仍需浏览器检查。

## 实际验证

- 本机真实权重离线生成资产：198 个候选、512 维；prepare 总耗时约 51 秒（包含索引编码和文件复制），其中索引完成后的查询检查约 1 秒。不是服务冷启动、稳态性能或服务器指标。资产包约 56.6 MB，位于被忽略的 deploy/retrieval.tar.gz，不进提交。
- 真实本地 BGE + FastAPI/SQLite：1 项通过，验证启动完成后连续两个新会话都实际向量召回、无回退，并复用已加载模型；没有真实 LLM/高德调用。该用例需显式 HFT_TEST_REAL_BGE=1，正常定向运行跳过。
- 检索、embedding、索引、启动器和部署先行回归 32 passed、1 skipped，首次新增测试因 Windows 默认 GBK 读取 UTF-8 失败；已显式修复测试编码。
- 修复后启动、天气/路线/地点/地图 Provider 及 Frozen 解释定向：33 passed、1 skipped。公网隔离/API/启动器/部署组合：78 passed、1 skipped、7 subtests passed。最后补齐资产白名单与组合配置测试后，最终启动/部署/启动器：21 passed、1 skipped。各轮有重叠，不相加为正式总指标。
- PowerShell 一键启动真实 BGE 前后端，完成预热与 readiness 后自动停止通过；最终确认 Check 数据库位于独立 .tmp/local-run 子目录。
- 没有改前端或评分，没有重跑前端整套测试；没有发布新 Frozen/Live 评测指标。Docker 在当前电脑不可用，新镜像尚未构建实测。

## 部署边界

服务器仍需上传代码 bundle 和独立资产包、同步旧 runtime.env 的 Hybrid/预热开关、构建 rag 镜像并运行严格冒烟。4GB 内存、冷启动、连续规划、真实高德 Key 与 SDK 在线加载、HTTPS 都未完成服务器验收。CPU Torch 镜像依赖与本机库版本不同，不能把本机结果等同于镜像通过。高德真实调用由部署者额度承担，既有限流不是商业预算系统。

完整步骤见 docs/current/deployment_retrieval.md。未更新服务器、未合并 main、未发布 Release；原有未跟踪草稿与临时目录保留。
