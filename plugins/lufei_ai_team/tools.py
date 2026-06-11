"""Agent-facing tools for the Lufei AI Team Kanban workflow."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from tools.registry import tool_error, tool_result

logger = logging.getLogger(__name__)


LUFEI_AI_TEAM_ORCHESTRATE_SCHEMA: dict[str, Any] = {
    "name": "lufei_ai_team_orchestrate",
    "description": (
        "Create a real Hermes Kanban swarm for the Lufei AI Team. Use this "
        "tool whenever the user asks to use 路飞 AI Team, 路飞知识水电站, Hermes "
        "Kanban 工作流, Elon Mask/ Larry Page/ Reed Hastings/ Steve Jobs/ Sam "
        "Altman 分工, or a full 小红书/客户咨询 workflow. Do not call "
        "xhs_extract_note directly for those requests; this tool creates the "
        "Larry/Reed/Jobs/Altman/Elon Kanban cards and attaches role-specific "
        "instructions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "input": {
                "type": "string",
                "description": (
                    "Raw user request, WeChat/meeting text, customer message, "
                    "or Xiaohongshu URL to classify and route."
                ),
            },
            "wiki_path": {
                "type": "string",
                "description": (
                    "Optional Obsidian llm-wiki root. Defaults to the local "
                    "creator wiki configured by HERMES_XHS_WIKI_PATH or the "
                    "local /Users/champion/Documents/develop/lufei/wiki vault."
                ),
            },
            "run_id": {
                "type": "string",
                "description": (
                    "Optional retry/test suffix appended to the Kanban "
                    "idempotency key. Leave empty for idempotent production "
                    "runs; set for explicit re-runs."
                ),
            },
            "dry_run": {
                "type": "boolean",
                "description": (
                    "When true, return the exact Kanban swarm/comment commands "
                    "without creating tasks."
                ),
                "default": False,
            },
        },
        "required": ["input"],
        "additionalProperties": False,
    },
}


def _load_lufei_module():
    # Keep import local so plugin discovery stays lightweight and so tests can
    # monkeypatch environment before the script resolves Hermes paths.
    from scripts import lufei_ai_team

    return lufei_ai_team


def _bool_arg(args: dict[str, Any], key: str, default: bool) -> bool:
    if key not in args or args.get(key) is None:
        return default
    value = args.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", ""}
    return bool(value)


async def lufei_ai_team_orchestrate_handler(args: dict[str, Any], **_: Any) -> str:
    """Classify a raw Lufei request and create the matching Kanban swarm."""

    raw_input = str(args.get("input") or "").strip()
    if not raw_input:
        return tool_error("input is required", success=False)
    try:
        module = _load_lufei_module()
        wiki_path = Path(args["wiki_path"]) if args.get("wiki_path") else module.DEFAULT_WIKI_PATH
        run_id = str(args["run_id"]).strip() if args.get("run_id") else None
        dry_run = _bool_arg(args, "dry_run", False)
        result = await asyncio.to_thread(
            module.orchestrate_from_input,
            raw_input,
            wiki_path=wiki_path,
            run_id=run_id,
            dry_run=dry_run,
        )
        return tool_result(
            {
                "success": True,
                "message": (
                    "Lufei AI Team Kanban swarm prepared"
                    if dry_run
                    else "Lufei AI Team Kanban swarm created"
                ),
                **result,
            }
        )
    except Exception as exc:
        logger.warning("lufei_ai_team_orchestrate failed: %s", exc, exc_info=True)
        return tool_error(str(exc), success=False)
