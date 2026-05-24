"""Lufei AI Team orchestration plugin.

This plugin exposes the file-first Lufei AI Team bootstrapper as an actual
Hermes tool. Without this tool, a normal chat session can read the
``lufei-ops-orchestrator`` skill and still decide to call low-level tools such
as ``xhs_extract_note`` directly. The orchestration tool gives the model a
single deterministic action that creates the Hermes Kanban swarm.
"""

from __future__ import annotations

from plugins.lufei_ai_team.tools import (
    LUFEI_AI_TEAM_ORCHESTRATE_SCHEMA,
    lufei_ai_team_orchestrate_handler,
)


def register(ctx) -> None:
    """Register Lufei AI Team tools with Hermes."""
    ctx.register_tool(
        name="lufei_ai_team_orchestrate",
        toolset="lufei",
        schema=LUFEI_AI_TEAM_ORCHESTRATE_SCHEMA,
        handler=lufei_ai_team_orchestrate_handler,
        is_async=True,
        description=LUFEI_AI_TEAM_ORCHESTRATE_SCHEMA.get("description", ""),
    )
