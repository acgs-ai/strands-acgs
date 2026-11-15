"""Run one allowed and one denied Strands tool call offline, then print the decision trail."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Any

# Disable telemetry before importing Strands; the model makes no service calls.
os.environ["OTEL_SDK_DISABLED"] = "true"

from strands import Agent, tool
from strands.models import Model

from strands_acgs import ACGSIntervention, GovernanceDecision


class AuditedIntervention(ACGSIntervention):
    """Collect decisions through the public API without retaining argument values."""

    def __init__(self) -> None:
        super().__init__()
        self.decisions: list[GovernanceDecision] = []

    def evaluate(self, tool_name: str, tool_input: Any) -> GovernanceDecision:
        decision = super().evaluate(tool_name, tool_input)
        self.decisions.append(decision)
        return decision


class ScriptedModel(Model):
    """Request two notes in successive turns, then finish without contacting an LLM."""

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
        if self.turn > 2:
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": "Done."}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
            return
        body = "The meeting starts at noon." if self.turn == 1 else "The admin password is demo-secret."
        yield {"contentBlockStart": {"start": {"toolUse": {"name": "record_note", "toolUseId": f"note-{self.turn}"}}}}
        yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps({"body": body})}}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use"}}


@tool
def record_note(body: str) -> str:
    """Record a demo note by printing it locally.

    Args:
        body: Note text.
    """
    print(f"Executed record_note: {body}")
    return "Note recorded."


def main() -> None:
    gate = AuditedIntervention()
    agent = Agent(
        model=ScriptedModel(),
        tools=[record_note],
        interventions=[gate],
        callback_handler=None,
    )
    agent("Record the two notes.")
    print("Audit trail (application decisions):")
    for decision in gate.decisions:
        print(json.dumps(asdict(decision), sort_keys=True))


if __name__ == "__main__":
    main()
