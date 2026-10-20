# SPDX-FileCopyrightText: 2026-present ACGS Contributors
#
# SPDX-License-Identifier: Apache-2.0
"""A scripted ``strands.models.Model``: replays assistant messages instead of calling an LLM. No API keys."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Iterable
from typing import Any

from strands.models import Model


def tool_use(name: str, tool_input: dict[str, Any], tool_use_id: str = "t1") -> dict[str, Any]:
    return {"toolUse": {"name": name, "toolUseId": tool_use_id, "input": tool_input}}


def tool_message(*uses: dict[str, Any]) -> dict[str, Any]:
    return {"role": "assistant", "content": list(uses)}


def text_message(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": [{"text": text}]}


class ScriptedModel(Model):
    """Streams pre-defined assistant messages in order and records the messages it receives."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.index = 0
        self.received: list[list[dict[str, Any]]] = []

    def update_config(self, **model_config: Any) -> None:
        return None

    def get_config(self) -> Any:
        return {}

    async def structured_output(self, *args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:  # pragma: no cover
        raise NotImplementedError
        yield

    async def stream(self, messages: Any, *args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
        self.received.append(json.loads(json.dumps(messages, default=str)))
        message = self.responses[self.index] if self.index < len(self.responses) else text_message("done")
        self.index += 1
        for event in self._events(message):
            yield event

    @staticmethod
    def _events(message: dict[str, Any]) -> Iterable[dict[str, Any]]:
        stop_reason = "end_turn"
        yield {"messageStart": {"role": "assistant"}}
        for content in message["content"]:
            if "text" in content:
                yield {"contentBlockStart": {"start": {}}}
                yield {"contentBlockDelta": {"delta": {"text": content["text"]}}}
                yield {"contentBlockStop": {}}
            if "toolUse" in content:
                stop_reason = "tool_use"
                use = content["toolUse"]
                yield {
                    "contentBlockStart": {"start": {"toolUse": {"name": use["name"], "toolUseId": use["toolUseId"]}}}
                }
                yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(use["input"])}}}}
                yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": stop_reason}}
