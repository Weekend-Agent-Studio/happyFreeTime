# 路线实际失败边界采集

_Current，2026-10-25，依赖 c7ce715。诊断改进，不是实际高德间歇故障已修复报告。_

服务器严格冒烟给出天气 1/1、路线 5/6 成功，第 2 个方案第 2 段降级。此前失败位置不同，单接口、坐标精度对照又成功。现有 upstream_failed_using_local_estimate 无法区分限流、超时与解析异常；继续重复冒烟不能代替实际失败证据。

本轮让 AmapResponseError 结构化保留安全的数字 infocode，原异常文本保持兼容。AmapRouteProvider 在异常进入 ResilientRouteProvider 前仅写一条 WARNING：amap_route_failure category=... code=... elapsed_ms=...。分类包括 API 拒绝、HTTP、超时、TLS、网络、非法 JSON、响应解析与未知异常；不记录上游正文、请求地址、地点/坐标或异常字符串。Response 构造校验也进入同一采集边界。正常响应不打印，失败缓存命中不重复打印。

不加重试、不改请求参数或坐标、不放宽严格检查，不改正常回退/缓存语义。通过 deploy/route-diagnostics.compose.yaml 可只读挂载 route.py 与 amap.py 到已有 API 镜像；文件不在容器启动时自动创建。诊断源文件使用单独 /opt/happyfreetime-route-diagnostics 目录，避免把 Git 工作树改脏。启用会重启 API、重新预热，并清除内存访客身份；这是显式诊断覆盖，不冒充业务镜像已经包含新提交。

诊断完去掉该 overlay 并用原 Compose 文件组合重新 up -d --no-build api，可恢复原镜像代码；正式修复后仍需按正常版本发布。部署的 BGE 模型及高德私有配置不变。

验证：安全日志、原有 Provider 与部署回归共 41 passed；包括 API/HTTP/超时/TLS/网络/解析分类、日志隐私、失败缓存不重复记录、只读挂载配置与既有回退语义。测试上游均为替身，不消耗真实高德额度；服务器的实际失败类别仍待采集。git diff --check 通过，未声称 Docker 挂载或实际根因已验证。
