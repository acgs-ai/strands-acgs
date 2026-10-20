# SPDX-FileCopyrightText: 2026-present ACGS Contributors
#
# SPDX-License-Identifier: Apache-2.0
"""The README example must be the example file, and the example file must run."""

from __future__ import annotations

import runpy
import textwrap
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "quickstart.py"


def test_readme_embeds_the_quickstart_example() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    body = EXAMPLE.read_text(encoding="utf-8").split('"""', 2)[2].strip()
    assert body in readme


def test_quickstart_example_runs(capsys: pytest.CaptureFixture[str]) -> None:
    runpy.run_path(str(EXAMPLE), run_name="__main__")
    out = capsys.readouterr().out
    assert "1: success: Sunny in Paris" in out
    assert "2: error: DENIED:" in out
    assert "ACGS-006" in out


def _python_blocks(markdown: str) -> list[str]:
    blocks, current, inside = [], [], False
    for line in markdown.splitlines():
        if line.strip() == "```python":
            inside, current = True, []
        elif inside and line.strip() == "```":
            inside = False
            blocks.append(textwrap.dedent("\n".join(current)))
        elif inside:
            current.append(line)
    return blocks


def test_every_readme_python_block_runs(capsys: pytest.CaptureFixture[str]) -> None:
    blocks = _python_blocks((ROOT / "README.md").read_text(encoding="utf-8"))
    assert len(blocks) >= 4
    for block in blocks:
        namespace: dict[str, Any] = {"__name__": "__readme__"}
        if "interruptResponse" in block:
            namespace["agent"] = _review_agent()
        exec(compile(block, "README.md", "exec", dont_inherit=True), namespace)  # noqa: S102
        if "interruptResponse" in block:
            assert namespace["result"].stop_reason == "end_turn"
            assert REVIEWED == ["disable logging"]
    out = capsys.readouterr().out
    assert "2: error: DENIED:" in out
    assert "ACGS-002" in out


REVIEWED: list[str] = []


def _review_agent() -> Any:
    from strands import Agent, tool

    from strands_acgs import ACGSIntervention
    from tests.fakes import ScriptedModel, text_message, tool_message, tool_use

    @tool
    def update_config(service: str, change: str) -> str:
        """Change a service configuration.

        Args:
            service: Service name.
            change: Requested change.
        """
        REVIEWED.append(change)
        return "updated"

    model = ScriptedModel(
        [
            tool_message(tool_use("update_config", {"service": "billing", "change": "disable logging"})),
            text_message("ok"),
        ]
    )
    return Agent(model=model, tools=[update_config], interventions=[ACGSIntervention()], callback_handler=None)
