from collections import Counter
import json
from pathlib import Path

from tests.test_public_demo import make_app, visitor, TestClient
from app.api.public_demo import PublicDemo


def stages(response):
    return Counter(item["stage"] for item in response["runtime_decisions"])


def test_recorded_showcases_keep_runtime_records_scoped_to_each_step():
    path = Path(__file__).resolve().parents[1] / "data/demo_showcases/v1.json"
    examples = {item["id"]: item for item in json.loads(path.read_text(encoding="utf-8"))["examples"]}
    assert examples["plan"]["steps"][1]["response"]["runtime_decisions"] == []
    expected = {"planning_intent": 1, "candidate_retrieval": 1, "recommendation_advisor": 1}
    assert stages(examples["plan"]["steps"][2]["response"]) == expected
    assert stages(examples["recovery"]["steps"][1]["response"]) == expected


def test_save_and_replan_report_only_work_from_the_current_request(tmp_path):
    with TestClient(make_app(tmp_path / "turns.db", PublicDemo())) as client:
        headers = visitor(client)
        session = client.post("/api/sessions", headers=headers).json()["data"]["session_id"]
        prefix = f"/api/sessions/{session}"
        initial = client.post(prefix + "/messages", headers=headers, json={"request_id": "initial-runtime-turn", "content": "今天下午出去玩"}).json()["data"]
        assert stages(initial)["planning_intent"] == 1
        saved = client.patch(prefix + "/planning-context", headers=headers, json={"request_id": "save-runtime-turn", "patch": {"base_revision": initial["planning_context"]["request_revision"], "who": {"adults": 2, "children": 0}}}).json()["data"]
        assert saved["status"] == "context_saved"
        assert saved["runtime_decisions"] == []
        replanned = client.post(prefix + "/planning-context/replan", headers=headers, json={"request_id": "replan-runtime-turn"}).json()["data"]
        assert replanned["plans"]
        assert stages(replanned) == {"planning_intent": 1, "candidate_retrieval": 1, "recommendation_advisor": 1}
        history = client.get(prefix, headers=headers).json()["data"]["response_history"]
        assert history[0]["runtime_decisions"] == initial["runtime_decisions"]


def test_recovery_resume_does_not_replay_pre_interrupt_model_records(tmp_path):
    with TestClient(make_app(tmp_path / "recovery.db", PublicDemo())) as client:
        headers = visitor(client)
        session = client.post("/api/sessions", headers=headers).json()["data"]["session_id"]
        url = f"/api/sessions/{session}/messages"
        initial = client.post(url, headers=headers, json={"request_id": "failed-runtime-turn", "content": "今天下午出去玩，人均1元，严格控制，不超预算"}).json()["data"]
        assert stages(initial)["planning_intent"] == 1
        recovery = initial["recovery"]
        action = recovery["actions"][0]
        payload = {"request_id": "resume-runtime-turn", "content": "确认调整", "recovery_action": {
            "action_id": action["action_id"], "interaction_id": recovery["interaction_id"],
            "request_revision": recovery["request_revision"], "plan_version_id": recovery.get("plan_version_id"),
        }}
        response = client.post(url, headers=headers, json=payload)
        assert response.status_code == 200, response.text
        resumed = response.json()["data"]
        assert resumed["plans"]
        assert stages(resumed) == {"planning_intent": 1, "candidate_retrieval": 1, "recommendation_advisor": 1}
        replay = client.post(url, headers=headers, json=payload)
        assert replay.status_code == 409  # Completed recovery cannot execute twice.
        history = client.get(f"/api/sessions/{session}", headers=headers).json()["data"]["response_history"]
        assert history[0]["runtime_decisions"] == initial["runtime_decisions"]
        assert history[-1]["runtime_decisions"] == resumed["runtime_decisions"]
