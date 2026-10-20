# strands-acgs

[![CI](https://github.com/acgs-ai/strands-acgs/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/acgs-ai/strands-acgs/actions/workflows/ci.yml)
[![PyPI - Version](https://img.shields.io/pypi/v/strands-acgs.svg)](https://pypi.org/project/strands-acgs)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/strands-acgs.svg)](https://pypi.org/project/strands-acgs)

Rule-based checks for the tool calls a [Strands Agents](https://strandsagents.com) agent makes, using the
[ACGS](https://pypi.org/project/acgs-lite/) constitution engine from `acgs-lite`.

`strands-acgs` provides one class, `ACGSIntervention`, an `InterventionHandler` that implements
`before_tool_call`. It returns the native intervention actions `Proceed`, `Deny` and `Confirm`, so denials and
human review use the Strands intervention and interrupt system. It does not wrap the agent, the model, or the tools.

- [Installation](#installation)
- [Usage](#usage)
- [How a tool call is evaluated](#how-a-tool-call-is-evaluated)
- [Human review](#human-review)
- [Custom constitutions](#custom-constitutions)
- [Limitations](#limitations)
- [Development](#development)
- [License](#license)

## Installation

```console
pip install strands-acgs
```

Requires Python 3.10+, `strands-agents>=1.43.0` (the first release with interventions) and `acgs-lite>=3.0.1`.

## Usage

The example below runs offline. `ScriptedModel` stands in for an LLM; in real use, pass any Strands model
provider instead, or omit `model` to use the default. The same file is in
[`examples/quickstart.py`](examples/quickstart.py) and is executed by the test suite.

```python
import json
from typing import Any

from strands import Agent, tool
from strands.models import Model

from strands_acgs import ACGSIntervention


@tool
def get_weather(city: str) -> str:
    """Return the weather for a city.

    Args:
        city: City name.
    """
    return f"Sunny in {city}"


@tool
def send_email(to: str, body: str) -> str:
    """Send an email.

    Args:
        to: Recipient.
        body: Message body.
    """
    return f"Email sent to {to}"


class ScriptedModel(Model):
    """Requests two tool calls on the first turn, then answers with text."""

    def __init__(self) -> None:
        self.turn = 0

    def update_config(self, **model_config: Any) -> None:
        pass

    def get_config(self) -> Any:
        return {}

    async def structured_output(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def stream(self, messages: Any, *args: Any, **kwargs: Any) -> Any:
        self.turn += 1
        yield {"messageStart": {"role": "assistant"}}
        if self.turn > 1:
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": "Done."}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
            return
        calls = [
            ("1", "get_weather", {"city": "Paris"}),
            ("2", "send_email", {"to": "ops@example.com", "body": "password: hunter2"}),
        ]
        for tool_use_id, name, tool_input in calls:
            yield {"contentBlockStart": {"start": {"toolUse": {"name": name, "toolUseId": tool_use_id}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(tool_input)}}}}
            yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use"}}


agent = Agent(
    model=ScriptedModel(),
    tools=[get_weather, send_email],
    interventions=[ACGSIntervention()],
    callback_handler=None,
)

agent("Check the weather in Paris and email ops the password.")
for message in agent.messages:
    for block in message["content"]:
        if "toolResult" in block:
            result = block["toolResult"]
            print(f"{result['toolUseId']}: {result['status']}: {result['content'][0]['text']}")
```

Output:

```text
1: success: Sunny in Paris
2: error: DENIED: Blocked by ACGS rule ACGS-006 (Agents must not expose sensitive data in responses).
```

The denied call never runs. Strands cancels it and returns an error tool result with the reason, so the model sees
why the call was refused. Argument values are not included in the reason.

## How a tool call is evaluated

1. `event.tool_use["name"]` and `event.tool_use["input"]` are rendered as text by `format_tool_call`:

   ```python
   from strands_acgs import format_tool_call

   print(format_tool_call("update_config", {"service": "billing", "change": "disable\nlogging", "dryRun": False}))
   ```

   ```text
   tool: update_config
   arguments:
   change: disable logging
   dryRun: false
   service: billing
   normalized: update config | dry run
   ```

   Each input leaf is one `path: value` line (nested values use `a.b[0].c` paths), so both argument names and
   values are visible to rules. Whitespace inside values is collapsed so phrases split across lines still match.
   Identifiers written in `snake_case`, `kebab-case` or `camelCase` (the tool name, argument names, and
   single-token string values) are repeated as plain words on the `normalized:` line, so the keyword
   `skip audit` also matches a tool called `skip_audit`. Nothing is truncated.
   Pass `tool_call_formatter=` to use a different representation.

2. The text is validated with `acgs_lite.GovernanceEngine.validate` against the constitution
   (`Constitution.default()` unless you pass one).

3. The result is mapped to an intervention action:

   | ACGS outcome | Action |
   |---|---|
   | No blocking rule matched (warn-only rules may match) | `Proceed` |
   | Only `require_human_review` / `escalate_to_senior` rules matched | `Confirm` (or `Deny` with `on_review="deny"`) |
   | Any `block`, `block_and_notify` or `halt_and_alert` rule matched | `Deny` |
   | A rule ID not found in the constitution, or an invalid result without a rule | `Deny` |
   | Any exception while reading or evaluating the tool call | `Deny` (fail-closed) |

   A matched blocking rule is never downgraded to review because a review rule matched as well; the denial reason
   then lists only the blocking rules after "Blocked by" and names the matched review rules separately. The
   handler's `on_error` is `"deny"`, so an exception that escapes the handler is also turned into a denial by
   Strands.

`ACGSIntervention.evaluate(tool_name, tool_input)` returns the underlying `GovernanceDecision`
(`verdict`, `reason`, `rule_ids`, `warning_rule_ids`, `error`) without running anything.

## Human review

Rules whose workflow action is `require_human_review` or `escalate_to_senior` (in the default constitution:
`ACGS-002` and `ACGS-005`) produce `Confirm`. Strands then stops the invocation with
`stop_reason == "interrupt"`; resume it with the human's answer:

```python
result = agent("Turn off logging for the billing service")
if result.stop_reason == "interrupt":
    interrupt = result.interrupts[0]          # interrupt.name == "acgs-governance"
    print(interrupt.reason)                   # which rule asked for review
    answer = "yes"                            # collect this from a person
    result = agent([{"interruptResponse": {"interruptId": interrupt.id, "response": answer}}])
```

By default `True`, `"y"` and `"yes"` approve; anything else cancels the call. Pass
`confirm_evaluate=` to change that, or `on_review="deny"` to deny review verdicts without asking.

## Custom constitutions

```python
from acgs_lite import Constitution, Rule, Severity, ViolationAction

from strands_acgs import ACGSIntervention

constitution = Constitution.from_rules(
    [
        Rule(id="NO-DESTRUCTIVE-SQL", text="No destructive SQL", severity=Severity.CRITICAL,
             keywords=["drop table", "truncate table"]),
        Rule(id="REFUND-REVIEW", text="Refunds need approval", severity=Severity.HIGH,
             keywords=["refund"], workflow_action=ViolationAction.REQUIRE_HUMAN_REVIEW),
    ],
    name="payments",
)
handler = ACGSIntervention(constitution=constitution, agent_id="payments-agent")
```

`Constitution.from_yaml("rules.yaml")` works as well. To register several handlers on one agent, give each a
distinct `name`.

## Limitations

- `Constitution.default()` focuses on governance integrity (self-approval, skipping audits, privilege escalation)
  and on secrets and personal data (`password`, `api_key`, `sk-...` and `ghp_...` tokens, US SSN
  patterns). It does **not** cover destructive operations such as `rm -rf` or `DROP TABLE`. Write rules for the
  tools you expose. `tests/test_default_constitution.py` lists what the default rules do and do not catch.
- Keyword rules in acgs-lite 3.0.1 match literal, case-insensitive substrings. They do not understand negation
  ("never share the password" matches `password`) and do not treat `-`, `_` and spaces as equivalent
  (`api-key` does not match the keyword `api_key`).
- Because argument names are part of the evaluated text, a tool with a parameter named `password` is denied under
  the default constitution whenever it is called.
- Interventions run in registration order and see changes made by earlier `Transform` actions. Handlers or
  plain hooks that modify `tool_use` after this handler are not re-checked, so register `ACGSIntervention` after
  any handler that rewrites tool input.

## Development

```console
hatch run prepare      # format, lint, mypy --strict, tests with coverage (fails under 95%)
hatch run lint         # ruff check + format check
hatch run typecheck    # mypy --strict
hatch run cov          # pytest with coverage
hatch run min:test     # tests against strands-agents 1.43.0 and acgs-lite 3.0.1
pre-commit install     # format/lint/typecheck/test on commit, commitizen on commit messages
```

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/) and are checked by commitizen.

## License

`strands-acgs` is distributed under the terms of the [Apache-2.0](LICENSE) license.
