# SPDX-FileCopyrightText: 2026-present ACGS Contributors
#
# SPDX-License-Identifier: Apache-2.0
"""ACGS governance as a Strands ``InterventionHandler``."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Literal

from acgs_lite import Constitution
from strands.hooks.events import BeforeToolCallEvent
from strands.interventions import Confirm, Deny, InterventionHandler, OnError, Proceed

from strands_acgs._governance import GovernanceDecision, ToolCallFormatter, ToolCallGovernor, Verdict

logger = logging.getLogger(__name__)

DEFAULT_NAME = "acgs-governance"


class ACGSIntervention(InterventionHandler):
    """Check every tool call against an ACGS constitution before the tool runs.

    Example:
        ```python
        from strands import Agent
        from strands_acgs import ACGSIntervention

        agent = Agent(tools=[...], interventions=[ACGSIntervention()])
        ```

    For each tool call the tool name and input are rendered with ``format_tool_call`` (or a custom
    ``tool_call_formatter``) and validated with ``acgs_lite.GovernanceEngine``. The verdict becomes a native
    intervention action:

    - ``allow`` returns ``Proceed``;
    - ``deny`` returns ``Deny``; Strands cancels the tool call and shows ``DENIED: <reason>`` to the model;
    - ``review`` returns ``Confirm`` (with ``on_review="confirm"``, the default), which pauses the agent through
      the Strands interrupt system until a human answers, or ``Deny`` (with ``on_review="deny"``).

    Errors raised while evaluating are turned into ``Deny`` inside the handler, and ``on_error`` is ``"deny"`` so
    that anything escaping the handler is also treated as a denial by the intervention registry.
    """

    def __init__(
        self,
        *,
        constitution: Constitution | None = None,
        agent_id: str = "strands-agent",
        on_review: Literal["confirm", "deny"] = "confirm",
        confirm_evaluate: Callable[[Any], bool] | None = None,
        tool_call_formatter: ToolCallFormatter | None = None,
        name: str = DEFAULT_NAME,
    ) -> None:
        """Create the handler.

        Args:
            constitution: The ACGS constitution to enforce. Defaults to ``acgs_lite.Constitution.default()``.
            agent_id: Agent identifier recorded by ACGS for each validation.
            on_review: What to do when only review rules match: ``"confirm"`` asks a human through
                ``Confirm``; ``"deny"`` denies the call.
            confirm_evaluate: Optional function that decides whether a human's interrupt response approves the
                call. Defaults to Strands' ``default_evaluate`` (``True``, ``"y"`` or ``"yes"``).
            tool_call_formatter: Optional function ``(tool_name, tool_input) -> str`` that replaces
                ``format_tool_call``.
            name: Handler name. It is also the interrupt name used for review requests. Use different names to
                register more than one ``ACGSIntervention`` on an agent.

        Raises:
            ValueError: If ``on_review`` is not ``"confirm"`` or ``"deny"``.
        """
        if on_review not in ("confirm", "deny"):
            msg = f"on_review must be 'confirm' or 'deny', got {on_review!r}"
            raise ValueError(msg)
        self._name = name
        self.on_review = on_review
        self.confirm_evaluate = confirm_evaluate
        self._governor = ToolCallGovernor(constitution, agent_id=agent_id, tool_call_formatter=tool_call_formatter)

    @property
    def name(self) -> str:
        """Unique name of this handler."""
        return self._name

    @property
    def on_error(self) -> OnError:
        """Treat any exception that escapes the handler as a denial."""
        return "deny"

    @property
    def constitution(self) -> Constitution:
        """The constitution being enforced."""
        return self._governor.constitution

    def evaluate(self, tool_name: str, tool_input: Any) -> GovernanceDecision:
        """Evaluate a tool call without running anything. Never raises.

        Args:
            tool_name: Name of the tool.
            tool_input: Input of the tool call.

        Returns:
            The ACGS governance decision.
        """
        return self._governor.evaluate(tool_name, tool_input)

    def before_tool_call(self, event: BeforeToolCallEvent, **kwargs: Any) -> Proceed | Deny | Confirm:
        """Map the ACGS verdict for the pending tool call to a Strands intervention action.

        Args:
            event: The before-tool-call event. ``event.tool_use`` provides the tool name and input.
            **kwargs: Reserved by Strands for future use.

        Returns:
            ``Proceed``, ``Deny`` or ``Confirm``.
        """
        try:
            tool_use = event.tool_use
            tool_name = str(tool_use["name"])
            decision = self.evaluate(tool_name, tool_use.get("input", {}))
        except Exception:
            logger.warning("ACGS could not read the pending tool call; denying it (fail-closed).", exc_info=True)
            return Deny(reason="ACGS governance could not evaluate this tool call, so it was denied.")
        return self._to_action(decision)

    def _to_action(self, decision: GovernanceDecision) -> Proceed | Deny | Confirm:
        if decision.verdict is Verdict.ALLOW:
            return Proceed(reason=decision.reason)
        if decision.verdict is Verdict.REVIEW and self.on_review == "confirm":
            prompt = f"Approve tool call '{decision.tool_name}'? {decision.reason}"
            if self.confirm_evaluate is not None:
                return Confirm(prompt=prompt, reason=decision.reason, evaluate=self.confirm_evaluate)
            return Confirm(prompt=prompt, reason=decision.reason)
        return Deny(reason=decision.reason)
