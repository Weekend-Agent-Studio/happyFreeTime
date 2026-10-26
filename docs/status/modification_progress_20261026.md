# 修改与恢复进度展示修正

_Current，2026-10-26。依赖 e7d3fbe 与 fb2003a，继续沿用 codex/route-fallback-latency，独立提交。_

## 变化与证据

Graph 在恢复动作返回 modify_plan 且产生有效候选时，没有进入 planning_node 的恢复完成逻辑，导致 recovery 阶段一直 started。新增回归通过真实 Graph 从 apply_recovery_action 继续执行修改节点，先捕获缺少 completed 的失败，再在修改成功且恢复仍打开时补齐结束事件；未进入恢复的普通修改不额外增加恢复事件。

前端原按 stage 名合并全轮事件，重试覆写旧状态而保留首次位置，并可能保留上一轮耗时。现在每次 started 创建独立步骤，terminal 更新对应步骤；重复执行按真实开始顺序保留。普通单次创建仍保留原七阶段占位，修改/恢复和重试只显示实际步骤。modify 和 recovery 的子步骤按执行时的父节点缩进；父阶段含子项时不再显示第二个旋转图标，实时提示优先最新进行中的步骤。回退阶段单独标为“已使用回退结果”，不表示整次失败；耗时以毫秒或一位小数秒显示。原始缺失结束事件不会被前端伪造 completed。

## 实际验证

- 后端先行回归 1 failed（恢复仅 started）；修正后 Graph/追踪 45 passed。新增普通修改对照后，API 恢复/替换与进度定向 8 passed、39 deselected。两批有重叠，不相加为总指标。
- 前端先行进度回归 2 failed、3 passed；修正后 App 与进度投影 87 passed。另加实时子步骤提示、父组无重复转圈的组件用例，定向 1 passed、77 skipped。
- 桌面与手机 Playwright 进度展示 2 passed。使用固定事件与模拟方案检查重试顺序、恢复完成、无残留转圈、20/40px 层级和 18.1 秒格式；不是实际高德耗时测试。截图：.impeccable/review/progress-recovery-desktop-chromium.png、progress-recovery-mobile-375.png。
- 固定多轮示例当前没有缺失结束事件，无需重录或倒写历史。
- 前端 TypeScript/生产构建与 git diff --check 通过。

本次不修改评分、约束、Provider 或恢复策略，只补齐真实成功路径的追踪事件及其展示。仍需服务器更新镜像后验收；没有以 UI 成功代替线上容量、模型或真实高德门槛。
