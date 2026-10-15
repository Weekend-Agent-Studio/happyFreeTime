# S-RX3 DemoWorld quality收口（2026-10-15）

## 范围

本切片只补 DemoWorld 的语义证据和确定性质量回归，不修改 Planner、Beam、Verifier 或 RecoveryPolicy。先用 Search Trace 区分结构/候选/验证问题；本轮确认现有规划链路已经能保留明确单站、午餐—活动—晚餐顺序和全天候选，因此没有用数据修改去掩盖 Planner 缺陷。

## 数据与夹具

- 新增 13 条版本化 POI semantic profile，覆盖北京特色、室内文化、浪漫、聊天、亲子和低疲劳等已观测缺口。
- Profile 仍明确标注为离线 DemoWorld fixture，不代表实时营业、路线或真实用户评价。
- 新增 `data/demo_world/v1/quality_fixtures.json`，包含 5 类质量断言：明确单站、午餐—活动—晚餐顺序、全天覆盖、短时段不凑站和 grounded profile anchors。
- 没有新增或伪造 POI 的硬事实；本轮只为已有 Catalog 身份补可追溯语义资料。

## 验证

- RX3 质量回归：5 passed。
- 后端全量：575 passed，78 subtests passed。
- `compileall app tests`：通过。
- `git diff --check`：通过。
- 明确单站仍返回单站；明确午餐—活动—晚餐保持顺序；全天首选方案覆盖上午、下午和晚间；短窗口不为凑站数增加站点。

## 检索索引

索引由当前 Profile 文件重新构建，二进制缓存保持在 Git 忽略目录，不提交模型文件。

- Profile JSON SHA256：`62d0dab83f923ff90db52f47c7da8dd3b8b143349ba580181a77997e6b6833bb`
- Quality fixture JSON SHA256：`bf4b3094466a63b82cbe661781fa4ba4fc6481774eeff66dc1c69dd4efa56304`
- Profile source hash：`250ba2f11a6c7f39b3c546a07870778599f8f972f08a3675c5d39ebd648a0593`
- Assembled profile hash：`24aa70e748fbc51b4c24a48c4f377c1d5b9fc3189a8cdc606ffafcc1b930ee35`
- Candidate count：200；chunk count：529
- Embedding model：`BAAI/bge-small-zh-v1.5`，dimension 512

## 边界

自动扩展半径的历史恢复样例仍因测试起点与 DemoWorld 最近候选距离不匹配而无法完成；它属于恢复 Fixture/数据覆盖问题，本轮没有通过扩大阈值或增加特例来伪造成功。S-RX4 再在干净提交上执行完整 Frozen C0–C4、Live B0/B3 和 RX reviewed HTTP 集合。
