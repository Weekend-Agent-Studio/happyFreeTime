import unittest

from app.services.model_errors import model_failure_reason


class StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__("provider response")
        self.status_code = status_code


class ModelErrorsTest(unittest.TestCase):
    def test_transport_and_provider_categories_are_stable(self) -> None:
        self.assertEqual(model_failure_reason(ConnectionError("secret details")), "network_error")
        self.assertEqual(model_failure_reason(TimeoutError("secret details")), "timeout")
        self.assertEqual(model_failure_reason(StatusError(401)), "auth_error:status=401")
        self.assertEqual(model_failure_reason(StatusError(429)), "rate_limited:status=429")
        self.assertEqual(model_failure_reason(StatusError(503)), "provider_error:status=503")

    def test_parser_category_does_not_include_exception_text(self) -> None:
        reason = model_failure_reason(ValueError("invalid output with an api key"))

        self.assertEqual(reason, "invalid_output")
        self.assertNotIn("api key", reason)


if __name__ == "__main__":
    unittest.main()
