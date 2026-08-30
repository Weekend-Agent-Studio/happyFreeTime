"""Small reproducibility and coverage check for Demo World V1."""

from __future__ import annotations

import json
from pathlib import Path

from build_demo_world import ANCHORS, OUTPUT, REPORT, build, completeness


def main() -> None:
    generated = build()
    stored = json.loads(OUTPUT.read_text(encoding="utf-8"))
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    anchors = json.loads(ANCHORS.read_text(encoding="utf-8"))["records"]
    assert stored == generated, "Demo World is stale; run scripts/build_demo_world.py"
    assert len(stored["records"]) == len(anchors) == 200
    assert {item["resource_id"] for item in stored["records"]} == {item["resource_id"] for item in anchors}
    required = {"reference_avg_price", "reference_rating", "reference_review_count", "open_hours", "gallery", "risk_tips"}
    assert all(required <= set(item) for item in stored["records"])
    assert report == completeness(stored), "Demo World completeness report is stale; run scripts/build_demo_world.py"
    print("Demo World V1 valid: 200/200 enrichments, deterministic build matches stored artifact")


if __name__ == "__main__":
    main()
