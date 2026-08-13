import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.domain.constraints import GeoLocation
from app.evaluation.smoke import load_cases, run_smoke_cases
from app.services.enrichment import EnvironmentContext


class SmokeEvalTest(unittest.TestCase):
    def test_all_offline_smoke_cases_pass(self) -> None:
        cases = load_cases(Path("evals/smoke_cases.json"))
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

        report = run_smoke_cases(cases, environment)

        self.assertEqual(report.total, 5)
        self.assertEqual(report.passed, report.total, report.model_dump())


if __name__ == "__main__":
    unittest.main()
