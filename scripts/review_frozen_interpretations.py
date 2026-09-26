"""Build a deterministic human-review packet for frozen Interpretation fixtures.

This utility never changes review_status.  It compares reviewed dataset labels
with captured draft fields and emits suggestions for a human reviewer.  It
does not include provider responses, prompts, credentials, or HTTP headers.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.evaluation.resume_release import DEFAULT_DATASET_PATH
from evals.frozen_interpretations import (
    FrozenInterpretationSet,
    frozen_fixture_file_sha256,
    input_sha256,
    load_frozen_interpretations,
)
from evals.resume_release_dataset import load_resume_release_dataset


def _constraint_summary(raw: Any) -> str:
    values: list[str] = []
    for field, label in (
        ("date_text", "date"),
        ("time_text", "time"),
        ("departure_at", "departure"),
        ("return_by", "return_by"),
        ("exact_stop_count", "stops"),
        ("budget_per_person", "budget"),
    ):
        value = getattr(raw, field, None)
        if value is not None:
            values.append(f"{label}={value}")
    roles = list(getattr(raw, "required_stop_roles", ()) or ())
    if roles:
        values.append("roles=" + ",".join(str(_enum_value(role)) for role in roles))
    members = list(getattr(raw, "members", ()) or ())
    if members:
        values.append("members=" + ",".join(members))
    if getattr(raw, "strict_budget", False):
        values.append("strict_budget=true")
    return "; ".join(values) or "—"


def _semantic_summary(raw: Any) -> str:
    parts: list[str] = []
    for field in ("preferences", "diet_tags", "scene_tags", "avoid"):
        values = list(getattr(raw, field, ()) or ())
        if values:
            parts.append(f"{field}={','.join(str(value) for value in values)}")
    return "; ".join(parts) or "—"


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _time_text(value: Any) -> str:
    return value.strftime("%H:%M") if hasattr(value, "strftime") else str(value)


def _expected_semantics(case: Any) -> set[str]:
    expected = case.expected
    return set(expected.expected_semantic_queries or ())


def _issues(case: Any, interpretation: Any, fixture_hash: str) -> list[str]:
    issues: list[str] = []
    raw = interpretation.raw_constraints
    expected = case.expected
    message = next(
        (step.user_input or "" for step in case.steps if step.action == "message"),
        "",
    )
    if fixture_hash != input_sha256(message):
        issues.append("hash mismatch")
    members = {str(value) for value in raw.members}
    scenes = {str(value) for value in raw.scene_tags}
    if members & {"约会", "约会对象", "情侣"}:
        issues.append("scene tag appears in members")
    if members & scenes:
        issues.append("members and scene_tags overlap")
    semantic_buckets = {
        field: {str(value) for value in getattr(raw, field)}
        for field in ("preferences", "diet_tags", "scene_tags")
    }
    if semantic_buckets["preferences"] & semantic_buckets["diet_tags"]:
        issues.append("preferences and diet_tags overlap")
    if semantic_buckets["preferences"] & semantic_buckets["scene_tags"]:
        issues.append("preferences and scene_tags overlap")
    if expected.expected_stop_count is not None and raw.exact_stop_count != expected.expected_stop_count:
        issues.append("stop count differs from reviewed label")
    if expected.required_roles:
        actual_roles = {_enum_value(role) for role in raw.required_stop_roles}
        if not set(expected.required_roles).issubset(actual_roles):
            issues.append("required stop role missing")
    if expected.expected_constraint_values:
        for field, expected_value in expected.expected_constraint_values.items():
            actual_value = getattr(raw, field, None)
            if field == "time_scope":
                actual_value = raw.time_scope.value if raw.time_scope else None
            if field == "time_window":
                # A role such as lunch supplies a deterministic Enrichment
                # anchor later in the pipeline.  It is not evidence that the
                # user explicitly stated a clock window in the Wire frame.
                if raw.explicit_time_window is None and any(
                    _enum_value(role) in {"lunch", "dinner", "meal"}
                    for role in raw.required_stop_roles
                ):
                    continue
                actual_value = (
                    {
                        "start": _time_text(raw.explicit_time_window.start),
                        "end": _time_text(raw.explicit_time_window.end),
                    }
                    if raw.explicit_time_window
                    else None
                )
            if actual_value != expected_value:
                issues.append(f"constraint {field} differs from reviewed label")
    missing_semantics = _expected_semantics(case) - (
        semantic_buckets["preferences"]
        | semantic_buckets["diet_tags"]
        | semantic_buckets["scene_tags"]
        | set(str(value) for value in raw.avoid)
    )
    if missing_semantics:
        issues.append("expected semantic evidence needs human review: " + ",".join(sorted(missing_semantics)))
    return issues


def build_packet(dataset_path: Path, fixture_path: Path) -> dict[str, Any]:
    dataset = load_resume_release_dataset(dataset_path)
    fixture_set = load_frozen_interpretations(fixture_path, require_reviewed=False)
    fixture_by_key = {(item.case_id, item.step_index): item for item in fixture_set.fixtures}
    rows: list[dict[str, Any]] = []
    for case in dataset.cases:
        if case.label_status != "reviewed":
            continue
        for step_index, step in enumerate(case.steps):
            if step.action != "message" or not step.user_input:
                continue
            fixture = fixture_by_key.get((case.case_id, step_index))
            if fixture is None:
                rows.append({"case_id": case.case_id, "step_index": step_index, "status": "missing"})
                continue
            interpretation = fixture.interpretation
            rows.append(
                {
                    "case_id": case.case_id,
                    "step_index": step_index,
                    "fixture_status": fixture.review_status,
                    "user_input_summary": step.user_input[:100],
                    "primary_intent": interpretation.primary_intent.value,
                    "constraints": _constraint_summary(interpretation.raw_constraints),
                    "semantics": _semantic_summary(interpretation.raw_constraints),
                    "command": interpretation.conversation_command.model_dump(mode="json")
                    if interpretation.conversation_command
                    else None,
                    "issues": _issues(case, interpretation, fixture.input_sha256),
                    "review_action": "keep reviewed"
                    if fixture.review_status == "reviewed"
                    else "human review required; do not auto-promote",
                }
            )
    return {
        "fixture_set_id": fixture_set.fixture_set_id,
        "fixture_sha256": frozen_fixture_file_sha256(fixture_path),
        "dataset_sha256": __import__("hashlib").sha256(dataset_path.read_bytes()).hexdigest(),
        "reviewed_fixture_count": sum(row.get("fixture_status") == "reviewed" for row in rows),
        "draft_fixture_count": sum(row.get("fixture_status") == "draft" for row in rows),
        "missing_fixture_count": sum(row.get("status") == "missing" for row in rows),
        "rows": rows,
    }


def write_markdown(packet: dict[str, Any], path: Path) -> None:
    groups = {"无争议": [], "建议修正": [], "需要用户判断": []}
    for row in packet["rows"]:
        if row.get("status") == "missing":
            groups["需要用户判断"].append(row)
        elif row.get("issues"):
            groups["需要用户判断"].append(row)
        elif row.get("fixture_status") == "draft":
            groups["建议修正"].append(row)
        else:
            groups["无争议"].append(row)
    lines = [
        "# Frozen Interpretation Resume V1 人工复核包",
        "",
        f"- Fixture set: `{packet['fixture_set_id']}`",
        f"- Fixture SHA256: `{packet['fixture_sha256']}`",
        f"- Dataset SHA256: `{packet['dataset_sha256']}`",
        f"- reviewed: {packet['reviewed_fixture_count']}",
        f"- draft: {packet['draft_fixture_count']}",
        f"- missing: {packet['missing_fixture_count']}",
        "",
        "> 本文件只提供确定性检查建议；draft 条目必须人工确认后才能改为 reviewed。",
        "",
    ]
    for title, rows in groups.items():
        lines.extend([f"## {title}", ""])
        if not rows:
            lines.append("（无）\n")
            continue
        lines.extend([
            "| case_id | 状态 | 用户输入摘要 | intent | 关键约束 | 语义字段 | 检查结果 |",
            "|---|---|---|---|---|---|---|",
        ])
        for row in rows:
            if row.get("status") == "missing":
                lines.append(f"| `{row['case_id']}` | missing | — | — | — | — | 缺少 Fixture |")
                continue
            issues = "; ".join(row["issues"]) or "无确定性异常；仍需人工确认"
            lines.append(
                "| `{case_id}` | {status} | {summary} | `{intent}` | {constraints} | {semantics} | {issues} |".format(
                    case_id=row["case_id"],
                    status=row["fixture_status"],
                    summary=row["user_input_summary"].replace("|", "\\|"),
                    intent=row["primary_intent"],
                    constraints=row["constraints"].replace("|", "\\|"),
                    semantics=row["semantics"].replace("|", "\\|"),
                    issues=issues.replace("|", "\\|"),
                )
            )
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    packet = build_packet(args.dataset, args.fixtures)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    (args.output.with_suffix(".json")).write_text(
        json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_markdown(packet, args.output)
    print(json.dumps({key: packet[key] for key in packet if key != "rows"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
