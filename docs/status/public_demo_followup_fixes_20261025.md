# 公网 Demo 补充条件与回放小修

_Current 状态记录，2026-10-25。沿用 `codex/public-demo-foundation`，依赖 da0098d；不是云端发布或真实模型验收。_

## 修正

- 空回复来自录制的 `context_saved` 响应；聊天气泡补充保存提示，实时发送也接纳没有 reply 的条件保存响应，避免仅剩“管家”标签。
- 原预算解析取整句第一个数字，且类型化补条件提案没有成人数字段。现在通过现有 ConstraintPatch、RequestPatchUpdateCompiler 与 ConstraintEngine 更新成人数，并优先解析预算相关数字；“改成两个人，人均150元，严格控制预算”保留 2 位成人和人均 150 元。明确总人数仅在该句无儿童词时作为成人数；不引入第二套规划或家庭结构解析。
- 恢复摘要不再拼接原始字段与 True/False，仍保留 field_changes；前端将字符串布尔值显示为“严格上限/参考条件”。示例复用真实会话的恢复状态还原逻辑，不再把完成的恢复展示为未处理。
- 用离线临时 HTTP/SQLite 重新录制案例；记录 ID 与时间变化属于重新录制，不修改旧用户历史。

## 验证

- 预算/人数与解释器定向回归：63 passed、10 subtests passed。
- 真实 API/SQLite 公网浏览器：6 passed、2 skipped（桌面专属场景在手机项目跳过）。包含原始问题句、示例空回复、恢复可读变化和只读零写请求。
- 已检查桌面截图：`.impeccable/review/public-demo-fixed-followup-desktop.png` 与 `public-demo-recovery-copy-desktop.png`。
- 最终 `App.test.tsx`：71 passed；API、公网隔离与恢复回归：82 passed、7 subtests passed；TypeScript 与生产构建通过；`git diff --check` 通过。没有运行 Frozen/Live 全量评测，没有真实模型调用。
- 修复过程中新增接口用例最初使用过短 request_id，触发 422；改为合法测试输入。新增前端空回复用例曾揭示发送入口丢弃无 reply 的条件保存响应，已修复并通过上述最终复验。上一轮构建因审批服务额度限制未执行，本轮已实际构建通过；不把被阻断的检查算作通过。

## 边界

规则模式理解仍有限；不承诺任意自然语言都可解析。现有用户历史不倒写，重新录制只更新固定示例。服务器仍需更新镜像；真实 Key、在线高德、公网 HTTPS 与 README 全面产品化留待后续。原有未跟踪草稿和临时目录不纳入本次提交。
