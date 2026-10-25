"""Record public, read-only examples from the real offline HTTP/SQLite chain.

Run from the repository root. No .env, credentials or user history is read.
"""
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient
from app.api.application import create_app
from app.domain.constraints import GeoLocation
from app.services.demo_router import DemoRouter
from app.services.demo_world import DemoCatalog
from app.services.enrichment import EnvironmentContext
from app.services.recommendation_advisor import RuleBasedRecommendationAdvisor
from app.services.planning_intent import RuleBasedPlanningIntentProvider
from app.providers.availability import DemoAvailabilityProvider
from app.providers.geocoding import build_geocoding_provider
from app.domain.providers import ProviderMode


def record_step(label, data, user=None):
    # Keep the canonical API response; no second plan/constraint projection.
    return {"label": label, "user": user, "response": data}


def main():
    with tempfile.TemporaryDirectory() as directory:
        catalog = DemoCatalog()
        environment = EnvironmentContext(now=datetime(2026, 8, 15, 10, tzinfo=ZoneInfo("Asia/Shanghai")), default_location=GeoLocation(city="北京市", district="朝阳区", address="北京市朝阳区", latitude=39.9219, longitude=116.4436))
        app = create_app(database_path=Path(directory) / "showcases.db", router=DemoRouter(), environment_provider=lambda _: environment,
                         catalog=catalog, geocoding_provider=build_geocoding_provider(mode=ProviderMode.MOCK, replay_path=Path(directory) / "geocoding.json"), availability_provider=DemoAvailabilityProvider(), poi_presentation_provider=catalog.presentation_provider,
                         recommendation_advisor=RuleBasedRecommendationAdvisor(), planning_intent_provider=RuleBasedPlanningIntentProvider())
        examples = []
        with TestClient(app) as client:
            def call(method, url, payload=None):
                response = client.request(method, url, json=payload)
                if response.status_code >= 400:
                    raise RuntimeError(f"Offline showcase request failed ({response.status_code}): {response.text}")
                response.raise_for_status()
                return response.json()["data"]
            for kind, title, description in [
                ("plan", "补充条件，再选方案", "生成方案后补充人数与预算，重新核验并正式选择。"),
                ("replace", "只替换其中一站", "保留其他站点，查看服务端产生的替换结果。"),
                ("recovery", "预算冲突与恢复", "查看不可行原因，由用户确认下一步调整。"),
            ]:
                session = call("POST", "/api/sessions")["session_id"]
                prefix = f"/api/sessions/{session}"
                content = "今天下午出去玩" if kind != "recovery" else "今天下午出去玩，人均1元，严格控制，不超预算"
                initial = call("POST", prefix + "/messages", {"request_id": kind + "-initial", "content": content})
                steps = [record_step("描述需求", initial, content)]
                if kind in ("plan", "replace"):
                    assert initial["plans"], f"{kind}: expected real plans"
                    plan = initial["plans"][0]
                if kind == "plan":
                    saved = call("PATCH", prefix + "/planning-context", {"request_id": "plan-save", "patch": {"base_revision": initial["planning_context"]["request_revision"], "who": {"adults": 2, "children": 0}, "budget": {"mode": "per_person", "amount": 150, "strict": True}}})
                    assert saved["planning_context"]["plan_stale"]
                    steps.append(record_step("补充人数与预算", saved, "改成两个人，人均150元，严格控制预算"))
                    planned = call("POST", prefix + "/planning-context/replan", {"request_id": "plan-replan"})
                    assert planned["plans"]
                    steps.append(record_step("明确重新规划", planned, "按新的条件重新规划"))
                    chosen = planned["plans"][0]
                    call("POST", prefix + f"/plans/{chosen['plan_id']}/select")
                    steps.append({"label": "正式选择", "user": "就选第一条方案", "reply": "服务端已保存正式选择。", "selected_plan_id": chosen["plan_id"]})
                elif kind == "replace":
                    call("POST", prefix + f"/plans/{plan['plan_id']}/select")
                    steps.append({"label": "正式选择", "user": "选择第一个方案", "reply": "服务端已保存正式选择。", "selected_plan_id": plan["plan_id"]})
                    stop = plan["stops"][0]
                    replacement = call("POST", prefix + "/messages", {"request_id": "replace-submit", "content": "替换第1站，其他站保持不变", "conversation_command": {"operation": "replace", "base_plan_id": plan["plan_id"], "base_plan_version_id": initial["plan_version_id"], "target": {"stop_index": 0, "resource_id": stop["resource_id"], "role": stop["role"], "raw_text": stop["name"]}, "replacement_criteria": []}})
                    steps.append(record_step("提交单站替换", replacement, "替换第1站，其他站保持不变"))
                elif kind == "recovery":
                    assert initial.get("recovery"), "expected a real recovery interaction"
                    action = initial["recovery"]["actions"][0]
                    recovered = call("POST", prefix + "/messages", {"request_id": "recovery-confirm", "content": "确认调整", "recovery_action": {"action_id": action["action_id"], "interaction_id": initial["recovery"]["interaction_id"], "request_revision": initial["recovery"]["request_revision"], "plan_version_id": initial["recovery"].get("plan_version_id")}})
                    steps.append(record_step("确认服务端建议：" + action["label"], recovered, "确认调整：" + action["label"]))
                examples.append({"id": kind, "title": title, "description": description, "prompt": content, "steps": steps})
        output = ROOT / "data/demo_showcases/v1.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({"recorded_date": "2026-08-15", "data_mode": "offline_demo", "examples": examples}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Recorded {len(examples)} offline HTTP showcase conversations.")


if __name__ == "__main__": main()
