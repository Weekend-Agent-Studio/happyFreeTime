# 模型配置与规划条件展示精修

_Current 状态记录，2026-10-25。基于 f5e9a49，沿用公网 Demo 分支；不涉及部署或真实 Key 调用验收。_

- 模型配置常显后端接收 Key、不持久保存、刷新重配与自付费用；其余原有接口/模型、期限与清理、HTTPS、数据模式及会话说明收进原生可展开说明。已配置用文字、图标和浅绿状态区呈现，不宣称 Key 已验证或本轮调用成功。
- 普通核验页显示该 AgentResponse 的 runtime_decisions 摘要：实际调用、规则、回退和缺记录分别展示，不泄露内部错误码或 token 诊断；原开发者详细记录保留。
- 四类偏好各有一条随选择变化的说明及示例，保持原类别与类型化提交路径。它们参与匹配/排序，不承诺所有自由标签都有目录证据，也不构成事实保证。
- 顶栏出发地直接显示服务端实际 name/address。当前默认事实为“北京市朝阳区”，坐标 GCJ-02 39.9219, 116.4436，没有已核实地标名，未补写为政府或商圈。编辑页保留固定演示点/非实时位置说明及坐标，重复地址不再展示两次。
- 取消首个日期条件按钮的 180px 强制最小宽度；长内容仍按原规则截断，完整内容可悬停查看。

验证：App 与 PlanningContextBar 定向组件 95 passed；最终 TypeScript/生产构建通过；真实本地 API/SQLite 浏览器 6 passed、2 skipped（手机跳过桌面专属场景）；`git diff --check` 通过。未改后端，未重跑后端或 Frozen/Live 评测。测试配置使用假 Key，只建立客户端而不发模型请求；真实调用成功与回退的展示通过组件响应夹具验证，不冒充本轮真实 Key 联调。

已分两轮检查桌面/手机截图：`.impeccable/review/public-demo-settings-configured-desktop.png`、`public-demo-settings-configured-mobile.png`、`public-demo-preferences-desktop.png`、`public-demo-empty-header-desktop.png`、`public-demo-fixed-followup-desktop.png`。第二轮仅确认最终缩短文案及现有行为，没有继续扩展视觉调整。

保留用户既有草稿与临时文件；未更新服务器、未合并或发布版本。
