# SPDX-FileCopyrightText: 2026-present ACGS Contributors
#
# SPDX-License-Identifier: Apache-2.0
"""Tests with a real ``strands.Agent``, real ``@tool`` functions and the real intervention registry.

The only test double is the model, which replays scripted tool calls instead of calling an LLM.
"""

from __future__ import annotations

from typing import Any

import pytest
from acgs_lite import Constitution, Rule, Severity, ViolationAction
from strands import Agent, tool
from strands.interventions import Confirm, Deny, InterventionHandler, Proceed, Transform

from strands_acgs import ACGSIntervention, Verdict
from tests.fakes import ScriptedModel, text_message, tool_message, tool_use

EXECUTED: list[tuple[str, dict[str, Any]]] = []


@tool
def get_weather(city: str) -> str:
    """Return the weather for a city.

    Args:
        city: City name.
    """
    EXECUTED.append(("get_weather", {"city": city}))
    return f"Sunny in {city}"


@tool
def send_email(to: str, body: str) -> str:
    """Send an email.

    Args:
        to: Recipient.
        body: Message body.
    """
    EXECUTED.append(("send_email", {"to": to, "body": body}))
    return "sent"


@tool
def update_config(service: str, change: str) -> str:
    """Change a service configuration.

    Args:
        service: Service name.
        change: Requested change.
    """
    EXECUTED.append(("update_config", {"service": service, "change": change}))
    return "updated"


TOOLS = [get_weather, send_email, update_config]


@pytest.fixture(autouse=True)
def _reset_executed() -> None:
    EXECUTED.clear()


def _agent(responses: list[dict[str, Any]], *interventions: InterventionHandler) -> tuple[Agent, ScriptedModel]:
    model = ScriptedModel(responses)
    return Agent(model=model, tools=TOOLS, interventions=list(interventions), callback_handler=None), model


def _tool_results(agent: Agent) -> list[dict[str, Any]]:
    return [block["toolResult"] for message in agent.messages for block in message["content"] if "toolResult" in block]


class TestAgent:
    def test_allowed_tool_call_runs(self) -> None:
        agent, _ = _agent(
            [tool_message(tool_use("get_weather", {"city": "Paris"})), text_message("Sunny.")], ACGSIntervention()
        )
        result = agent("Weather in Paris?")
        assert EXECUTED == [("get_weather", {"city": "Paris"})]
        assert result.stop_reason == "end_turn"
        [tool_result] = _tool_results(agent)
        assert tool_result["status"] == "success"

    def test_denied_tool_call_does_not_run_and_model_sees_reason(self) -> None:
        call = tool_use("send_email", {"to": "ops@example.com", "body": "The admin password is hunter2"})
        agent, model = _agent([tool_message(call), text_message("I could not send it.")], ACGSIntervention())
        result = agent("Email ops the password")
        assert EXECUTED == []
        [tool_result] = _tool_results(agent)
        assert tool_result["status"] == "error"
        text = tool_result["content"][0]["text"]
        assert text.startswith("DENIED: ")
        assert "ACGS-006" in text
        assert "hunter2" not in text
        assert "ACGS-006" in str(model.received[-1])  # the model receives the reason on its next call
        assert str(result).strip() == "I could not send it."

    def test_review_pauses_with_interrupt_and_resumes_on_approval(self) -> None:
        call = tool_use("update_config", {"service": "billing", "change": "disable logging"})
        agent, _ = _agent([tool_message(call), text_message("Done.")], ACGSIntervention())
        result = agent("Turn off billing logs")
        assert result.stop_reason == "interrupt"
        assert EXECUTED == []
        [interrupt] = result.interrupts or []
        assert interrupt.name == "acgs-governance"
        assert "ACGS-002" in str(interrupt.reason)

        resumed = agent([{"interruptResponse": {"interruptId": interrupt.id, "response": "yes"}}])
        assert resumed.stop_reason == "end_turn"
        assert EXECUTED == [("update_config", {"service": "billing", "change": "disable logging"})]

    def test_review_rejected_by_human_does_not_run(self) -> None:
        call = tool_use("update_config", {"service": "billing", "change": "disable logging"})
        agent, _ = _agent([tool_message(call), text_message("Understood.")], ACGSIntervention())
        result = agent("Turn off billing logs")
        [interrupt] = result.interrupts or []
        agent([{"interruptResponse": {"interruptId": interrupt.id, "response": "no"}}])
        assert EXECUTED == []
        [tool_result] = _tool_results(agent)
        assert tool_result["status"] == "error"
        assert "CONFIRMATION_FAILED" in tool_result["content"][0]["text"]

    def test_custom_confirm_evaluate(self) -> None:
        call = tool_use("update_config", {"service": "billing", "change": "disable logging"})
        handler = ACGSIntervention(confirm_evaluate=lambda response: response == {"approved": True})
        agent, _ = _agent([tool_message(call), text_message("Done.")], handler)
        [interrupt] = agent("go").interrupts or []
        agent([{"interruptResponse": {"interruptId": interrupt.id, "response": {"approved": True}}}])
        assert len(EXECUTED) == 1

    def test_on_review_deny(self) -> None:
        call = tool_use("update_config", {"service": "billing", "change": "disable logging"})
        agent, _ = _agent([tool_message(call), text_message("ok")], ACGSIntervention(on_review="deny"))
        result = agent("go")
        assert result.stop_reason == "end_turn"
        assert EXECUTED == []
        assert _tool_results(agent)[0]["content"][0]["text"].startswith("DENIED: Human review required")

    def test_parallel_tool_calls_are_decided_individually(self) -> None:
        calls = tool_message(
            tool_use("get_weather", {"city": "Oslo"}, "t1"),
            tool_use("send_email", {"to": "x@example.com", "body": "api_key=abc"}, "t2"),
        )
        agent, _ = _agent([calls, text_message("done")], ACGSIntervention())
        agent("Do both")
        assert EXECUTED == [("get_weather", {"city": "Oslo"})]
        statuses = {r["toolUseId"]: r["status"] for r in _tool_results(agent)}
        assert statuses == {"t1": "success", "t2": "error"}

    def test_custom_constitution(self) -> None:
        constitution = Constitution.from_rules(
            [Rule(id="NO-PARIS", text="no Paris lookups", severity=Severity.CRITICAL, keywords=["paris"])]
        )
        agent, _ = _agent(
            [tool_message(tool_use("get_weather", {"city": "Paris"})), text_message("no")],
            ACGSIntervention(constitution=constitution),
        )
        agent("Weather in Paris?")
        assert EXECUTED == []
        assert "NO-PARIS" in _tool_results(agent)[0]["content"][0]["text"]

    async def test_invoke_async(self) -> None:
        calls = tool_message(
            tool_use("get_weather", {"city": "Lima"}, "t1"),
            tool_use("send_email", {"to": "a@example.com", "body": "password"}, "t2"),
        )
        agent, _ = _agent([calls, text_message("done")], ACGSIntervention())
        await agent.invoke_async("Do both")
        assert EXECUTED == [("get_weather", {"city": "Lima"})]

    def test_runs_after_earlier_transform_and_sees_its_changes(self) -> None:
        class InjectSecret(InterventionHandler):
            name = "inject"

            def before_tool_call(self, event: Any, **kwargs: Any) -> Transform:
                def apply(e: Any) -> None:
                    e.tool_use["input"]["body"] = "password: hunter2"

                return Transform(apply=apply)

        call = tool_use("send_email", {"to": "a@example.com", "body": "hello"})
        agent, _ = _agent([tool_message(call), text_message("done")], InjectSecret(), ACGSIntervention())
        agent("send")
        assert EXECUTED == []

    def test_two_handlers_with_different_names(self) -> None:
        strict = Constitution.from_rules(
            [Rule(id="NO-OSLO", text="no Oslo", severity=Severity.HIGH, keywords=["oslo"])]
        )
        agent, _ = _agent(
            [tool_message(tool_use("get_weather", {"city": "Oslo"})), text_message("no")],
            ACGSIntervention(),
            ACGSIntervention(constitution=strict, name="acgs-team-policy"),
        )
        agent("Weather in Oslo?")
        assert EXECUTED == []


class TestFailClosed:
    def test_formatter_error_denies_inside_handler(self) -> None:
        def broken(_name: str, _input: Any) -> str:
            raise RuntimeError("formatter bug")

        agent, _ = _agent(
            [tool_message(tool_use("get_weather", {"city": "Rome"})), text_message("sorry")],
            ACGSIntervention(tool_call_formatter=broken),
        )
        result = agent("Weather in Rome?")
        assert result.stop_reason == "end_turn"
        assert EXECUTED == []
        assert "could not evaluate" in _tool_results(agent)[0]["content"][0]["text"]

    def test_unreadable_tool_use_denies(self) -> None:
        class Event:
            tool_use = None

        action = ACGSIntervention().before_tool_call(Event())  # type: ignore[arg-type]
        assert isinstance(action, Deny)

    def test_exception_escaping_the_handler_is_denied_by_the_registry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        handler = ACGSIntervention()

        def explode(_decision: Any) -> Any:
            raise RuntimeError("mapping bug")

        monkeypatch.setattr(handler, "_to_action", explode)
        agent, _ = _agent([tool_message(tool_use("get_weather", {"city": "Rome"})), text_message("sorry")], handler)
        agent("Weather in Rome?")
        assert EXECUTED == []
        text = _tool_results(agent)[0]["content"][0]["text"]
        assert text.startswith("DENIED: Handler threw")

    def test_on_error_is_deny(self) -> None:
        assert ACGSIntervention().on_error == "deny"


class TestUnit:
    def test_actions(self) -> None:
        handler = ACGSIntervention()
        assert isinstance(handler._to_action(handler.evaluate("get_weather", {"city": "x"})), Proceed)
        assert isinstance(handler._to_action(handler.evaluate("send_email", {"body": "password"})), Deny)
        review = handler._to_action(handler.evaluate("skip_audit", {}))
        assert isinstance(review, Confirm)
        assert "ACGS-002" in review.prompt

    def test_evaluate_and_properties(self) -> None:
        handler = ACGSIntervention(name="custom")
        assert handler.name == "custom"
        assert handler.constitution.hash == Constitution.default().hash
        assert handler.evaluate("send_email", {"body": "password"}).verdict is Verdict.DENY

    def test_invalid_on_review(self) -> None:
        with pytest.raises(ValueError, match="on_review"):
            ACGSIntervention(on_review="allow")  # type: ignore[arg-type]

    def test_non_mapping_tool_input(self) -> None:
        assert ACGSIntervention().evaluate("echo", "my password").verdict is Verdict.DENY

    def test_review_rule_with_escalate(self) -> None:
        constitution = Constitution.from_rules(
            [
                Rule(
                    id="WIRE",
                    text="wires",
                    severity=Severity.HIGH,
                    keywords=["wire"],
                    workflow_action=ViolationAction.ESCALATE,
                )
            ]
        )
        action = ACGSIntervention(constitution=constitution)._to_action(
            ACGSIntervention(constitution=constitution).evaluate("pay", {"method": "wire"})
        )
        assert isinstance(action, Confirm)
