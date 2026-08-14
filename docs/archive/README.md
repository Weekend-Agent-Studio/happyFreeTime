# HappyFreeTime 历史文档归档

_保留 V1、旧 Router 方案和原始讨论，用于理解设计演进_

---

> ⚠️ **非当前依据：** 本目录中的内容可能与当前代码、测试和 V2 架构冲突。新会话不得从这里提取需求并自动实现；如需采用其中想法，必须重新核对当前基线并由用户确认。

## 📚 归档内容

| 目录 | 内容 | 当前替代入口 |
| --- | --- | --- |
| [`v1/`](v1/) | Hackathon 阶段 Mock 设计和迁移说明 | [`../canonical/architecture_v2.md`](../canonical/architecture_v2.md) |
| [`router/`](router/) | 多版 RouterExtractor 设计和旧问答 | 当前 `app/services/router_extractor.py` 与 M1 学习复盘 |
| [`transcripts/`](transcripts/) | 未提炼的原始协作讨论 | [`../collaboration/playbook.md`](../collaboration/playbook.md) |

## 🛡️ 归档规则

- 保留原文，除顶部状态说明外不重写历史结论
- 不从归档材料直接更新代码
- 引用归档时同时链接当前替代文档
- 若历史材料含独有且仍有效的知识，先迁移到正式文档，再考虑是否删除原文
