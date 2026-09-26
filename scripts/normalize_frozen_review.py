"""Apply the manually approved normalization to the Resume V1 fixture set."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


REVIEW_NOTE = "human-reviewed against case input and expected evaluation labels"


def _fixture_by_id(payload: dict, case_id: str) -> dict:
    for fixture in payload["fixtures"]:
        if fixture["case_id"] == case_id and fixture["step_index"] == 0:
            return fixture
    raise ValueError(f"missing fixture: {case_id}")


def _set_scene_only(fixture: dict, *, evidence: str = "约会") -> None:
    raw = fixture["interpretation"]["raw_constraints"]
    raw["members"] = []
    raw["scene_tags"] = ["约会"]
    evidence_map = fixture["interpretation"].setdefault("evidence_map", {})
    evidence_map.pop("members", None)
    evidence_map["scene_tags"] = evidence


def normalize(path: Path) -> tuple[str, str, int]:
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    payload = json.loads(path.read_text(encoding="utf-8"))

    lunch = _fixture_by_id(payload, "plan_lunch_only_structure")
    # The user said only "午饭".  The 11:30–14:00 anchor belongs to
    # deterministic Enrichment, not to the Router Wire Interpretation.
    lunch["interpretation"]["raw_constraints"]["explicit_time_window"] = None
    lunch["interpretation"]["raw_constraints"]["time_scope"] = None

    child = _fixture_by_id(payload, "plan_child_indoor_explore")
    child_raw = child["interpretation"]["raw_constraints"]
    child_raw["preferences"] = ["能互动探索"]
    child_raw["scene_tags"] = ["室内"]
    child_evidence = child["interpretation"].setdefault("evidence_map", {})
    child_evidence["preferences"] = "能互动探索"
    child_evidence["scene_tags"] = "室内"

    for case_id in (
        "clarify_ambiguous_location",
        "modify_dinner_less_spicy",
        "modify_two_rounds_one_at_a_time",
    ):
        _set_scene_only(_fixture_by_id(payload, case_id))

    for fixture in payload["fixtures"]:
        fixture["review_status"] = "reviewed"
        fixture["review_note"] = REVIEW_NOTE

    payload["fixtures"] = sorted(
        payload["fixtures"], key=lambda item: (item["case_id"], item["step_index"])
    )
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    after = hashlib.sha256(path.read_bytes()).hexdigest()
    return before, after, len(payload["fixtures"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args()
    before, after, count = normalize(args.fixture)
    print(json.dumps({"before": before, "after": after, "fixtures": count}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
