# 模型记录恢复与显式高德接入

_Current，2026-10-25，依赖 e5de00c，沿用公网分支；评分和权重仅讨论，没有实现。_

- Git 核对：f5e9a49 已保留 RuntimeCallSummary 与折叠模型调用卡片，未删除；仅由开发者开关隐藏。上一提交新增的简版列表本轮移除，统一复用旧卡片。
- 需要确认风险移到概览之前；模型记录在概览下方默认折叠。普通用户可看图标、阶段、实际调用、阶段总耗时和尝试次数；原始诊断与 token 仍需开发者开关。耗时不能当成纯模型推理时长。
- `start_local.ps1 -WithAmap` 对应 Python `--with-amap`：显式读取三项高德凭证，仅启用 JS 地图和可选真实出发地搜索。保留公网访客隔离、地图专属 cookie、站主 LLM 屏蔽与模拟路线/天气。没有新建地图或搜索实现。
- 确认 Docker 默认 rule 召回，仅安装 requirements.txt；未包含 requirements-rag.txt、模型和向量索引。BGE 通过首次有效查询惰性加载并在同进程缓存，Graph/PlanningService 在应用创建时实例化，不按新会话重建。没有修改召回或增加自动预热。

验证：前端定向 5 passed（69 非目标用例跳过），启动器 8 passed，地图 Provider/API 4 passed；本地真实 API 浏览器 6 passed、2 设备条件 skipped；构建通过。地图新增用例确认无模型 Key 的公网访客可使用地图代理，但地图 cookie 无法访问会话 API。地图上游为测试替身，没有真实高德联网验收。未重新运行 Frozen/Live 或扩大到评分改动。

桌面截图：`.impeccable/review/public-demo-runtime-expanded-desktop.png`。服务器未更新、PR 未合并；本机有效高德凭证及实际地图加载仍待用户环境验证。
