"""Minimal provider boundary for the original PlannerAgent.

It classifies provider failures without retaining raw responses.  Planning
strategy remains in PlannerAgent; this adapter only normalizes the call
boundary and exposes an explicit failure instead of fabricating a plan.
"""


def classify_provider_error(exc, messages=None) -> str:
    name = type(exc).__name__.lower()
    status = getattr(exc, "status_code", None)
    if "tool" in name or "schema" in name:
        return "tool_schema_rejected"
    if "sequence" in name or "message" in name:
        return "message_sequence_invalid"
    if "parameter" in name or "unsupported" in name:
        return "unsupported_parameter"
    if status == 400 or "invalidrequest" in name or "badrequest" in name:
        return "provider_request_invalid"
    return "provider_request_invalid"


def _flatten_tool_context(messages):
    """Turn provider-sensitive tool-role history into ordinary context.

    Some OpenAI-compatible legacy endpoints reject a second request containing
    assistant tool calls.  Keeping the tool results as a user-visible context
    message preserves the facts while avoiding a second tool-call protocol.
    """
    import json
    from langchain_core.messages import HumanMessage

    tool_results = []
    base = []
    for message in messages:
        if getattr(message, "type", None) == "tool":
            tool_results.append({
                "name": getattr(message, "name", None),
                "content": getattr(message, "content", ""),
            })
        elif getattr(message, "type", None) == "ai" and getattr(message, "tool_calls", None):
            continue
        else:
            base.append(message)
    if tool_results:
        base.append(HumanMessage(
            content="本地工具已完成调用。以下是工具结果，请只基于这些结果输出最终 JSON 方案，不要再次调用工具："
            + json.dumps(tool_results, ensure_ascii=False)
        ))
    return base


class LegacyProviderAdapter:
    def __init__(self, client, retry_client=None):
        self.client = client
        self.retry_client = retry_client

    def invoke(self, messages):
        try:
            return self.client.invoke(messages)
        except Exception as exc:
            # Provider-compatible repair is intentionally bounded to one
            # retry.  The adapter never rewrites a response into a plan.
            if self.retry_client is not None and getattr(exc, "status_code", None) == 400:
                try:
                    has_tool_context = any(getattr(message, "type", None) == "tool" for message in messages)
                    retry_messages = _flatten_tool_context(messages) if has_tool_context else messages
                    return self.retry_client.invoke(retry_messages)
                except Exception as retry_exc:
                    exc = retry_exc
            return {
                "ok": False,
                "error_code": classify_provider_error(exc, messages),
                "error_type": type(exc).__name__,
            }
