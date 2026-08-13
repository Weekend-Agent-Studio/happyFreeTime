"""不调用外部模型的确定性 Router，用于本地开发、演示和离线回归。"""

from __future__ import annotations

import re

from app.domain.constraints import Intent, Interpretation, RawConstraints
from app.services.router_extractor import RouterContext


class DemoRouter:
    """用小型规则集实现与真实 Router 相同的 ``interpret`` 接口。

    它不是生产级中文理解器，只覆盖 README 中列出的演示表达。价值在于无需
    API Key 也能真实走完 Graph、反问、规划、持久化和前端链路。
    """

    def interpret(self, user_input: str, context: RouterContext) -> Interpretation:
        text = user_input.strip()
        if text in {"你好", "嗨", "谢谢", "你是谁"}:
            return Interpretation(
                primary_intent=Intent.CHITCHAT,
                intent_scores={Intent.CHITCHAT: 1.0},
                reply="你好，我可以帮你规划活动、餐厅和两站之间的行程。",
            )

        # 反问答案会与原问题拼成一段文本重新进入 Router，因此正则可以同时
        # 处理初始请求和“用户补充：人均300”这种恢复后的输入。
        budget_match = re.search(r"(?:人均|每人)\s*(\d{2,4})", text)
        distance_text = next(
            (phrase for phrase in ("步行可达", "附近", "别太远") if phrase in text),
            None,
        )
        date_text = next(
            (phrase for phrase in ("今天", "明天", "后天", "周六") if phrase in text),
            None,
        )
        time_text = next(
            (phrase for phrase in ("上午", "中午", "下午", "晚上") if phrase in text),
            None,
        )
        party_size = 2 if any(word in text for word in ("朋友", "约会", "对象")) else None
        preferences = [
            label
            for keyword, label in (
                ("轻松", "轻松"),
                ("甜品", "甜品"),
                ("安静", "安静"),
                ("拍照", "适合拍照"),
            )
            if keyword in text
        ]
        strict_budget = any(
            phrase in text for phrase in ("别超预算", "严格预算", "不能超预算")
        )
        raw = RawConstraints(
            date_text=date_text,
            time_text=time_text,
            adults=party_size,
            budget_text=budget_match.group(0) if budget_match else None,
            budget_per_person=int(budget_match.group(1)) if budget_match else None,
            strict_budget=strict_budget,
            max_distance_text=distance_text,
            preferences=preferences,
            scene_tags=["约会"] if "约会" in text else [],
        )
        # 与真实 Router 一样保留证据映射，方便后续评测抽取是否有依据。
        evidence = {
            field: value
            for field, value in {
                "date_text": date_text,
                "time_text": time_text,
                "budget_per_person": budget_match.group(0) if budget_match else None,
                "max_distance_text": distance_text,
            }.items()
            if value is not None
        }
        return Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 1.0},
            raw_constraints=raw,
            extraction_confidence={field: 1.0 for field in evidence},
            evidence_map=evidence,
        )
