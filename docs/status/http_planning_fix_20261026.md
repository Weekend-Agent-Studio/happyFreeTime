# 公网 HTTP 规划兼容修复

_Current，2026-10-26。从 main@b430599 建立 codex/http-planning-fix，服务器尚未更新。_

## 原因与变化

App 在发送入口和消息、条件保存、重新规划中直接使用 crypto.randomUUID。公网 IP 的 HTTP 页面不是安全上下文，该接口不存在，首次 submit 在 try 之前抛错；地图不依赖此路径。Key 输入禁用是 DemoSettings 的预期 HTTP 防护，与该故障不同。

统一用 createClientId：支持时使用原生 randomUUID，否则使用 getRandomValues 的 16 字节构造 UUID v4，设置版本与 variant，不使用 Math.random，不改变服务端访客 capability。请求失败重试沿用原 request_id 的逻辑保持原样；不改变 HTTP 下 Key 禁用规则或后端。

## 验证

- 先行两个组件回归在缺少 randomUUID 时均失败，准确捕获 TypeError 与未生成方案。
- 修正后 App 与 UUID 定向测试 79 passed。资源竞争时已有复杂恢复测试触发 5 秒超时，串行以 maxWorkers=1、testTimeout=15000 重跑通过；不将其耗时作为性能指标。
- Playwright 使用 hft-http.test 映射到本机离线 API，实际断言 isSecureContext=false、randomUUID 不存在；手动输入/首页快捷规划在桌面和移动均成功，Key 输入禁用，无页面异常，4 passed。配置仅为本地测试域名放行 Vite Host 并绕过本机代理；首次未绕过代理时 HTTP 502，不计通过。
- 生产构建通过；首次与其他验证并行时本机内存不足，限制 GOMAXPROCS=2 后重跑成功。git diff --check 通过。
- 截图在 .impeccable/review/http-planning-{desktop-public,mobile-public}-{typed,suggestion}.png。

不涉及真实付费 LLM 或高德调用，不代表服务器、公网 HTTPS 或正式上线验收。服务器当前 5b94e86d 的后端无此业务变更，更新不需要重新准备 BGE 资产。
