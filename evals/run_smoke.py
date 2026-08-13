import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.domain.constraints import GeoLocation
from app.evaluation.smoke import load_cases, run_smoke_cases
from app.services.enrichment import EnvironmentContext


def main() -> int:
    cases_path = Path(__file__).with_name("smoke_cases.json")
    environment = EnvironmentContext(
        now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        default_location=GeoLocation(
            city="北京市",
            district="朝阳区",
            address="北京市朝阳区",
            latitude=39.9219,
            longitude=116.4436,
        ),
    )
    report = run_smoke_cases(load_cases(cases_path), environment)
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if report.passed == report.total else 1


if __name__ == "__main__":
    raise SystemExit(main())

