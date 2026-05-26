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
    assert "creator-ops-orchestrator" in soul


def test_xhs_content_swarm_routes_existing_pipeline() -> None:
    module = load_module()
    command = module.swarm_command(
        "xhs-content",
        source="https://www.xiaohongshu.com/explore/example",
        run_id="retry-1",
    )
    command_text = module.shell_join(command)

    assert Path(command[0]).name == "hermes"
    assert command[1:3] == ["kanban", "swarm"]
    assert any(part.startswith("lufei-page:") for part in command)
    assert any(part.startswith("lufei-hastings:") for part in command)
    assert "creator-data-intake" in command_text
    assert "creator-content-studio" in command_text
    assert "--verifier lufei-altman" in command_text
    assert "--synthesizer lufei-ceo" in command_text
    assert "run:retry-1" in command_text


def test_task_body_names_acceptance_criteria() -> None:
    module = load_module()
    body = module.task_body("portfolio-review", source="test input")

    assert "分析作品集叙事" in body
    assert "路飞最终判断权" in body
    assert "test input" in body


def test_xhs_note_context_handles_existing_and_new_raw(tmp_path) -> None:
    module = load_module()
    note_id = "69da513a0000000023005dfa"
    url = f"https://www.xiaohongshu.com/explore/{note_id}?xsec_source=pc_user"

    missing = module.xhs_note_context_block(url, wiki_path=tmp_path)

    assert f"note_id: `{note_id}`" in missing
    assert "status: `raw_missing`" in missing
    assert "xhs_extract_note" in missing
    assert "xhs_ingest_note_to_wiki" in missing
    assert str(tmp_path / "raw" / "xhs" / "notes" / note_id) in missing

    raw_dir = tmp_path / "raw" / "xhs" / "notes" / note_id
    raw_dir.mkdir(parents=True)
    (raw_dir / "note.json").write_text(
        '{"stats":{"comment_count":0},"comment_threads":{"status":"empty","items":[]}}',
        encoding="utf-8",
    )
    (raw_dir / "note.md").write_text("# note", encoding="utf-8")

    ready = module.xhs_note_context_block(url, wiki_path=tmp_path)

    assert "status: `raw_ready`" in ready
    assert "Fast path" in ready
    assert "must not use broad `search_files`" in ready

    (raw_dir / "note.json").write_text(
        '{"stats":{"comment_count":226},"comment_threads":{"status":"skipped_disabled","items":[]}}',
        encoding="utf-8",
    )

    stale = module.xhs_note_context_block(url, wiki_path=tmp_path)

    assert "status: `raw_comments_stale`" in stale
    assert "comment refresh needed: `True`" in stale
    assert "Refresh-comments path" in stale
    assert "extract_comments=true" in stale


def test_block_command_keeps_seed_task_safe() -> None:
    module = load_module()
    command = module.block_command("t_example")

    assert Path(command[0]).name == "hermes"
    assert command[1:3] == ["kanban", "block"]
    assert command[3] == "t_example"
    assert "安全种子任务" in command[4]


def test_xhs_worker_instruction_mentions_existing_skills() -> None:
    module = load_module()

    instruction = module.worker_instruction(
        "xhs-content",
        "lufei-hastings",
        source="https://www.xiaohongshu.com/explore/69da513a0000000023005dfa",
    )

    assert "xhs-viral-analysis" in instruction
    assert "xhs-topic-selection" in instruction
    assert "xhs-script-generation" in instruction
    assert "69da513a0000000023005dfa" in instruction
    assert "exact raw dir" in instruction
    assert "为什么会点赞" in instruction
    assert "不能编数字" in instruction


def test_create_swarm_adds_role_comments() -> None:
    module = load_module()
    calls: list[list[str]] = []

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if Path(command[0]).name == "hermes" and command[1:3] == ["kanban", "swarm"]:
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
    assert Path(calls[0][0]).name == "hermes"
    assert calls[0][1:3] == ["kanban", "swarm"]
    assert calls[0][-1] == "--json"
    comment_bodies = "\n".join(call[4] for call in calls[1:])
    assert "xhs_extract_note" in comment_bodies
    assert "Sam Altman 质量门" in comment_bodies
    assert "Elon Mask 终局综合" in comment_bodies


def test_customer_consultation_routes_to_cs_crm_diagnosis_and_gate() -> None:
    module = load_module()

    assert module.classify_orchestration_intent("老师，我想咨询作品集辅导多少钱") == "portfolio-review"
    assert module.classify_orchestration_intent("我有简历和作品集，先帮我判断适合什么服务") == "customer-consultation"
    assert module.classify_orchestration_intent("老师，我想问一下课程和一对一价格") == "customer-consultation"

    command = module.swarm_command(
        "customer-consultation",
        source="老师，我想问一下课程和一对一价格",
        run_id="case-1",
    )
    command_text = module.shell_join(command)

    assert "creator-member-cs" in command_text
    assert "creator-service-diagnosis" in command_text
    assert "--verifier lufei-altman" in command_text


def test_orchestrate_from_input_dry_run_classifies_xhs_link() -> None:
    module = load_module()

    result = module.orchestrate_from_input(
        "请拆解 https://www.xiaohongshu.com/explore/example 并生成逐字稿",
        run_id="dry",
        dry_run=True,
    )

    assert result["task_type"] == "xhs-content"
    assert "swarm_command" in result["result"]


def test_sync_profile_skills_links_role_skills(tmp_path, monkeypatch) -> None:
    module = load_module()
    hermes_home = tmp_path / ".hermes"
    skills_path = tmp_path / "xhs-content-pipeline"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    for skill_name in module.LUFEI_SKILL_NAMES:
        skill_dir = skills_path / "skills" / skill_name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {skill_name}\ndescription: test.\n---\n",
            encoding="utf-8",
        )

    result = module.sync_profile_skills(skills_path=skills_path)

    assert result["ok"] is True
    target = hermes_home / "profiles" / "lufei-ceo" / "skills" / "lufei" / "creator-ops-orchestrator"
    assert target.is_symlink()
    assert (target / "SKILL.md").exists()
