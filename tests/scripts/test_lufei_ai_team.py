from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
import subprocess


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "lufei_ai_team.py"


def load_module():
    spec = importlib.util.spec_from_file_location("lufei_ai_team", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_roles_use_tech_leader_codenames() -> None:
    module = load_module()

    codenames = {role.codename for role in module.ROLES}
    assert {
        "Elon Mask",
        "Steve Jobs",
        "Larry Page",
        "Reed Hastings",
        "Jeff Bezos",
        "Satya Nadella",
        "Sam Altman",
    } <= codenames
    assert len({role.profile for role in module.ROLES}) == len(module.ROLES)


def test_render_soul_contains_lufei_operating_boundaries() -> None:
    module = load_module()
    soul = module.render_soul(module.role_by_profile("lufei-ceo"))

    assert "路飞设计沉思录知识水电站" in soul
    assert "Chairman: 路飞本人" in soul
    assert "不自动发布小红书" in soul
    assert "Kanban" in soul


def test_xhs_content_swarm_routes_existing_pipeline() -> None:
    module = load_module()
    command = module.swarm_command(
        "xhs-content",
        source="https://www.xiaohongshu.com/explore/example",
        run_id="retry-1",
    )
    command_text = module.shell_join(command)

    assert command[:3] == ["hermes", "kanban", "swarm"]
    assert any(part.startswith("lufei-page:") for part in command)
    assert any(part.startswith("lufei-hastings:") for part in command)
    assert "--verifier lufei-altman" in command_text
    assert "--synthesizer lufei-ceo" in command_text
    assert "run:retry-1" in command_text


def test_task_body_names_acceptance_criteria() -> None:
    module = load_module()
    body = module.task_body("portfolio-review", source="test input")

    assert "分析作品集叙事" in body
    assert "路飞最终判断权" in body
    assert "test input" in body


def test_block_command_keeps_seed_task_safe() -> None:
    module = load_module()
    command = module.block_command("t_example")

    assert command[:3] == ["hermes", "kanban", "block"]
    assert command[3] == "t_example"
    assert "安全种子任务" in command[4]


def test_xhs_worker_instruction_mentions_existing_skills() -> None:
    module = load_module()

    instruction = module.worker_instruction(
        "xhs-content",
        "lufei-hastings",
        source="https://www.xiaohongshu.com/explore/example",
    )

    assert "xhs-viral-analysis" in instruction
    assert "xhs-topic-selection" in instruction
    assert "xhs-script-generation" in instruction
    assert "为什么会点赞" in instruction
    assert "不能编数字" in instruction


def test_create_swarm_adds_role_comments() -> None:
    module = load_module()
    calls: list[list[str]] = []

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[:3] == ["hermes", "kanban", "swarm"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    '{"root_id":"t_root","worker_ids":["t_page","t_hastings","t_jobs"],'
                    '"verifier_id":"t_altman","synthesizer_id":"t_ceo"}'
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout=f"Comment added to {command[3]}\n", stderr="")

    result = module.create_swarm_with_instructions(
        "xhs-content",
        source="https://www.xiaohongshu.com/explore/example",
        runner=fake_runner,
    )

    assert result["comments_added"] == 5
    assert calls[0][:3] == ["hermes", "kanban", "swarm"]
    assert calls[0][-1] == "--json"
    comment_bodies = "\n".join(call[4] for call in calls[1:])
    assert "xhs_extract_note" in comment_bodies
    assert "Sam Altman 质量门" in comment_bodies
    assert "Elon Mask 终局综合" in comment_bodies
