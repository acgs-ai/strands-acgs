"""Run a Strands Agent whose tool calls are checked by ACGS before they execute.

This example runs offline: ``ScriptedModel`` stands in for an LLM and requests two tool calls.
Replace it with any Strands model provider (for example ``BedrockModel``) in real use.
"""

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
