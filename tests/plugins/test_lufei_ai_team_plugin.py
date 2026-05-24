import asyncio
import json
from pathlib import Path

from plugins.lufei_ai_team import register
from plugins.lufei_ai_team import tools as lufei_tools


def test_lufei_ai_team_plugin_registers_orchestrator_tool() -> None:
    calls: list[dict] = []

    class Ctx:
        def register_tool(self, **kwargs):
            calls.append(kwargs)

    register(Ctx())

    assert len(calls) == 1
    assert calls[0]["name"] == "lufei_ai_team_orchestrate"
    assert calls[0]["toolset"] == "lufei"
    assert calls[0]["is_async"] is True
    assert "xhs_extract_note directly" in calls[0]["description"]


def test_lufei_ai_team_orchestrate_dry_run_creates_kanban_swarm_command(tmp_path) -> None:
    raw = asyncio.run(
        lufei_tools.lufei_ai_team_orchestrate_handler(
            {
                "input": "请使用路飞 AI Team 拆解 https://www.xiaohongshu.com/explore/example",
                "wiki_path": str(tmp_path / "wiki"),
                "run_id": "unit",
                "dry_run": True,
            }
        )
    )
    payload = json.loads(raw)

    assert payload["success"] is True
    assert payload["task_type"] == "xhs-content"
    command = payload["result"]["swarm_command"]
    assert "kanban swarm" in command
    assert "lufei-page:" in command
    assert "lufei-hastings:" in command
    assert "--verifier lufei-altman" in command
    assert "--synthesizer lufei-ceo" in command
    assert "run:unit" in command
