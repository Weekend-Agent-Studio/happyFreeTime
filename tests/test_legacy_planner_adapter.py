from Agents.PlannerAgent.provider_adapter import (
    LegacyProviderAdapter,
    classify_provider_error,
)


class InvalidRequest(Exception):
    status_code = 400
    body = None


def test_provider_error_classification_is_safe_and_stable():
    assert classify_provider_error(InvalidRequest()) == "provider_request_invalid"


def test_adapter_does_not_return_fake_plan_on_provider_failure():
    class FailingClient:
        def invoke(self, messages):
            raise InvalidRequest("provider rejected request")

    adapter = LegacyProviderAdapter(FailingClient())
    result = adapter.invoke([])

    assert result["ok"] is False
    assert result["error_code"] == "provider_request_invalid"
    assert "plans" not in result


def test_adapter_allows_one_bounded_provider_retry():
    class FlakyClient:
        def __init__(self):
            self.calls = 0

        def invoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                raise InvalidRequest()
            return {"ok": True}

    client = FlakyClient()
    result = LegacyProviderAdapter(client, retry_client=client).invoke([])
    assert result == {"ok": True}
    assert client.calls == 2
