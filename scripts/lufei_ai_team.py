#!/usr/bin/env python3
"""Bootstrap the Lufei AI operating team for Hermes.

The script keeps the implementation intentionally file-first:

* Hermes profiles carry the role/persona through SOUL.md and profile
  descriptions so the Kanban decomposer can route work.
* Kanban is the only orchestration entrypoint. This script can render safe
  commands and, when explicitly requested, create a blocked seed task.
* The lufei-xhs-wiki remains the long-term source of truth for assets,
  concepts, CRM summaries, and content outputs.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


DEFAULT_WIKI_PATH = Path(
    "/Users/champion/Documents/develop/Wiki/ClaudeWiki/lufei-xhs-wiki"
)
DEFAULT_SKILLS_PATH = Path("/Users/champion/Documents/develop/skills/xhs-content-pipeline")


@dataclass(frozen=True)
class Role:
    profile: str
    codename: str
    title: str
    description: str
    owns: tuple[str, ...]
    cannot: tuple[str, ...]


ROLES: tuple[Role, ...] = (
    Role(
        profile="lufei-ceo",
        codename="Elon Mask",
        title="CEO Agent",
        description=(
            "Lufei IP operating CEO. Extracts intent from WeChat, meetings, "
            "voice notes, and links; creates Hermes Kanban tasks with goals, "
            "constraints, workers, and acceptance criteria."
        ),
        owns=(
            "intent extraction from WeChat DM, voice notes, Tencent Meeting, and XHS links",
            "Kanban task creation, routing, priority, and acceptance criteria",
            "final synthesis for Lufei/azan before human review",
        ),
        cannot=(
            "publish content automatically",
            "change Lufei persona without Chairman approval",
            "expose private CRM fields to external customer channels",
        ),
    ),
    Role(
        profile="lufei-jobs",
        codename="Steve Jobs",
        title="Product and Experience",
        description=(
            "Turns Lufei's design-career expertise into crisp service flows, "
            "diagnosis templates, content packages, and future SKU prototypes."
        ),
        owns=(
            "interview debrief, resume review, and portfolio review flow design",
            "diagnostic report structure and user-facing wording",
            "service boundary design for future paid SKUs",
        ),
        cannot=(
            "price or launch paid SKUs in phase one",
            "replace Lufei's final professional judgment",
        ),
    ),
    Role(
        profile="lufei-page",
        codename="Larry Page",
        title="Search and Ingestion",
        description=(
            "Searches, captures, and indexes XHS notes, competitors, comments, "
            "live replays, Tencent Meeting assets, course material, and raw files."
        ),
        owns=(
            "xhs_extract_note and xhs_extract_profile_notes",
            "xhs_ingest_account_to_wiki and xhs_build_wiki_manifest",
            "Tencent Meeting and Youdao raw asset completeness checks",
        ),
        cannot=(
            "perform batch engagement, commenting, or publishing",
            "treat uncited material as verified knowledge",
        ),
    ),
    Role(
        profile="lufei-hastings",
        codename="Reed Hastings",
        title="Content Growth",
        description=(
            "Owns the Lufei content engine: viral analysis, serial topics, "
            "script rhythm, retention hooks, collection points, and comment design."
        ),
        owns=(
            "xhs-viral-analysis ten-dimensional breakdown",
            "xhs-topic-selection 50 candidates to Top 5",
            "xhs-script-generation annotated scripts",
            "content A/B hypotheses and weekly topic briefs",
        ),
        cannot=(
            "invent performance data",
            "make the account sound unlike Lufei",
            "auto-post to XHS",
        ),
    ),
    Role(
        profile="lufei-bezos",
        codename="Jeff Bezos",
        title="Customer Success",
        description=(
            "Scans WeChat customer support, XHS comments, CRM notes, and usage "
            "feedback; turns demand signals into backlog and follow-up briefs."
        ),
        owns=(
            "customer-service entry triage for future paid SKUs",
            "CRM context lookup and follow-up backlog",
            "comment-intelligence and demand mining",
        ),
        cannot=(
            "give high-stakes final career promises",
            "leak raw meeting transcripts or private CRM fields",
        ),
    ),
    Role(
        profile="lufei-nadella",
        codename="Satya Nadella",
        title="Platform Integration",
        description=(
            "Keeps Hermes, Obsidian, WeChat, Tencent Meeting, XHS tools, and "
            "local model/provider configuration working as one operating system."
        ),
        owns=(
            "Hermes plugin and gateway integration",
            "Obsidian/llm-wiki file adapter and manifests",
            "deployment checks on Lufei's local machine",
        ),
        cannot=(
            "change product scope without Elon Mask task approval",
            "store secrets in wiki markdown",
        ),
    ),
    Role(
        profile="lufei-altman",
        codename="Sam Altman",
        title="AI Quality Gate",
        description=(
            "Verifies citations, confidence, persona consistency, privacy, and "
            "model behavior before outputs become official wiki knowledge or "
            "customer-facing drafts."
        ),
        owns=(
            "citation and provenance checks",
            "confidence labeling and hallucination rejection",
            "staging-to-official wiki merge gate",
            "persona similarity checks against persona.md",
        ),
        cannot=(
            "silently accept uncited claims",
            "merge private student data into public-facing concepts",
        ),
    ),
)


TASK_TEMPLATES: dict[str, dict[str, object]] = {
    "xhs-content": {
        "title": "拆解小红书内容并生成下一条选题与逐字稿",
        "workers": ("lufei-page", "lufei-hastings", "lufei-jobs"),
        "verifier": "lufei-altman",
        "synthesizer": "lufei-ceo",
        "acceptance": (
            "note.json / note.md / transcript / comments 已入库",
            "完成 xhs-viral-analysis 十维拆解",
            "完成 xhs-topic-selection 50 候选到 Top 5",
            "完成 xhs-script-generation 互动标注版逐字稿",
            "Sam Altman 校验引用、置信度和路飞人设",
        ),
    },
    "interview-debrief": {
        "title": "根据学员面试过程生成复盘草稿",
        "workers": ("lufei-bezos", "lufei-jobs", "lufei-page"),
        "verifier": "lufei-altman",
        "synthesizer": "lufei-ceo",
        "acceptance": (
            "提取面试时间线、题目、回答、卡点和面试官意图",
            "查询 CRM 与相似会议上下文",
            "输出可由路飞审核的复盘建议与下一步训练动作",
            "标注置信度和引用来源",
        ),
    },
    "resume-review": {
        "title": "根据简历和目标岗位生成修改建议",
        "workers": ("lufei-bezos", "lufei-jobs", "lufei-page"),
        "verifier": "lufei-altman",
        "synthesizer": "lufei-ceo",
        "acceptance": (
            "提取目标岗位、经历证据、项目亮点和缺口",
            "按 UI/UX/Brand 求职场景给出修改建议",
            "不得凭空承诺面试或 offer 结果",
            "输出路飞可二次判断的版本",
        ),
    },
    "portfolio-review": {
        "title": "根据作品集材料生成结构化点评",
        "workers": ("lufei-bezos", "lufei-jobs", "lufei-page"),
        "verifier": "lufei-altman",
        "synthesizer": "lufei-ceo",
        "acceptance": (
            "分析作品集叙事、项目价值、设计过程、视觉呈现和追问风险",
            "查询相似学员会议和路飞既有方法论",
            "输出分层问题清单与修改优先级",
            "保留路飞最终判断权",
        ),
    },
    "feedback-backlog": {
        "title": "扫描评论、微信反馈与 CRM，沉淀需求 backlog",
        "workers": ("lufei-bezos", "lufei-page", "lufei-jobs"),
        "verifier": "lufei-altman",
        "synthesizer": "lufei-ceo",
        "acceptance": (
            "提取客户反复问的问题、转化卡点和内容机会",
            "生成 queries/feedback-backlog-<date>.md",
            "区分内部改进、内容选题、未来 SKU 线索",
            "不暴露敏感学员信息",
        ),
    },
}


def hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def profiles_root() -> Path:
    return hermes_home() / "profiles"


def role_by_profile(profile: str) -> Role:
    for role in ROLES:
        if role.profile == profile:
            return role
    raise KeyError(profile)


def render_soul(role: Role, wiki_path: Path = DEFAULT_WIKI_PATH) -> str:
    owns = "\n".join(f"- {item}" for item in role.owns)
    cannot = "\n".join(f"- {item}" for item in role.cannot)
    return f"""# {role.codename} · {role.title}

你是路飞设计沉思录知识水电站里的 {role.codename}，Hermes profile 为 `{role.profile}`。

## 工作对象

- Chairman: 路飞本人，负责最终专业判断、内容方向和客户承诺。
- CEO Agent: Elon Mask 负责把微信、会议、语音、链接里的意图转成 Kanban 任务。
- 长期知识库: `{wiki_path}`
- 账号定位: UI/UX 设计求职、作品集、简历、面试、笔试、Web Coding 与大厂设计思维。

## 你负责

{owns}

## 你不能做

{cannot}

## 输出要求

- 所有事实判断必须尽量引用 wiki/raw、CRM、会议、XHS 笔记或用户明确输入。
- 不确定时标注 `confidence: low|medium|high`。
- 面向客户的回答要保护隐私；面向路飞的内部分析可以更直接。
- 不自动发布小红书，不自动给客户最终承诺。
- 重要产物先写入 `staging/` 或 Kanban 评论，等待 Sam Altman/Turing 位校验后再进入正式知识页。
"""


def task_body(task_type: str, *, source: str | None = None, wiki_path: Path = DEFAULT_WIKI_PATH) -> str:
    if task_type not in TASK_TEMPLATES:
        raise KeyError(task_type)
    template = TASK_TEMPLATES[task_type]
    acceptance = "\n".join(f"- {item}" for item in template["acceptance"])  # type: ignore[index]
    source_line = source or "(由 Elon Mask 从微信/会议/语音中补充)"
    return f"""## Intent

{template["title"]}

## Source

{source_line}

## Wiki

{wiki_path}

## Required Workers

{", ".join(template["workers"])} -> {template["verifier"]} -> {template["synthesizer"]}

## Acceptance Criteria

{acceptance}
"""


def swarm_command(
    task_type: str,
    *,
    source: str | None = None,
    wiki_path: Path = DEFAULT_WIKI_PATH,
    run_id: str | None = None,
) -> list[str]:
    if task_type not in TASK_TEMPLATES:
        raise KeyError(task_type)
    template = TASK_TEMPLATES[task_type]
    goal = f'{template["title"]} :: {source or "manual"}'
    idempotency_key = f"lufei:{task_type}:{source or 'manual'}"
    if run_id:
        idempotency_key = f"{idempotency_key}:run:{run_id}"
    cmd = [
        "hermes",
        "kanban",
        "swarm",
        goal,
        "--tenant",
        "lufei",
        "--created-by",
        "lufei-ceo",
        "--idempotency-key",
        idempotency_key,
    ]
    for worker in template["workers"]:  # type: ignore[index]
        if worker == "lufei-page":
            title = "资料提取与 wiki 入库"
        elif worker == "lufei-hastings":
            title = "爆款拆解、选题与逐字稿"
        elif worker == "lufei-jobs":
            title = "服务流程与用户体验方案"
        elif worker == "lufei-bezos":
            title = "客户信号、CRM 与反馈归纳"
        else:
            title = "专项分析"
        cmd.extend(["--worker", f"{worker}:{title}"])
    cmd.extend(["--verifier", str(template["verifier"])])
    cmd.extend(["--synthesizer", str(template["synthesizer"])])
    return cmd


def comment_command(task_id: str, body: str, *, author: str = "lufei-ceo") -> list[str]:
    return [
        "hermes",
        "kanban",
        "comment",
        task_id,
        body,
        "--author",
        author,
    ]


def _acceptance_text(task_type: str) -> str:
    template = TASK_TEMPLATES[task_type]
    return "\n".join(f"- {item}" for item in template["acceptance"])  # type: ignore[index]


def worker_instruction(
    task_type: str,
    profile: str,
    *,
    source: str | None = None,
    wiki_path: Path = DEFAULT_WIKI_PATH,
) -> str:
    source_text = source or "(等待 Elon Mask 从微信/会议/语音补充原始输入)"
    common = (
        f"Source: {source_text}\n"
        f"Wiki: {wiki_path}\n"
        "Phase boundary: 先做知识沉淀、内容生产、诊断草稿和人工审核；不自动发布，不自动承诺收费 SKU 结果。\n"
    )
    if task_type == "xhs-content":
        if profile == "lufei-page":
            return (
                "执行说明 / Larry Page:\n"
                f"{common}\n"
                "1. 使用 `xhs_extract_note` 提取单条小红书图文/视频笔记、OCR、转写、评论和互动数据。\n"
                "2. 注意 profile worker 的 `$HERMES_HOME` 是隔离缓存；如果 `/Users/champion/.hermes/cache/xiaohongshu/<note_id>/` 已有更完整版本（视频、字幕、OCR、评论、stats），必须优先用全局完整版本入库，避免用 profile 私有缓存覆盖完整 raw。\n"
                "3. 将 note.json / note.md / transcript / comments 写入 "
                f"`{wiki_path}/raw/xhs/notes/<note_id>/`；保留 Hermes cache 路径。\n"
                "4. 运行或触发 `xhs_build_wiki_manifest`，让下游 skill 能通过 manifest/query 读取上下文。\n"
                "5. 输出必须列出：note_id、标题、media/transcript/comment 状态、warnings、raw 路径。\n"
                "6. 禁止做批量互动、刷评、发布或任何账号操作。"
            )
        if profile == "lufei-hastings":
            return (
                "执行说明 / Reed Hastings:\n"
                f"{common}\n"
                "1. 基于 raw note 与 wiki 上下文，跑/复用 `xhs-viral-analysis` 十维拆解。\n"
                "2. 明确回答：开头如何留人/提高完播；为什么会点赞；哪里触发收藏；哪里触发评论；结尾如何导向私信/群/店铺。\n"
                "3. 使用 `xhs-topic-selection` 产出 50 个候选并筛 Top 5，说明每个为什么可能火。\n"
                "4. 使用 `xhs-script-generation` 生成 1 条互动设计标注版逐字稿。\n"
                "5. 没有抓到的点赞/收藏/评论数据必须写 `missing`，不能编数字。"
            )
        if profile == "lufei-jobs":
            return (
                "执行说明 / Steve Jobs:\n"
                f"{common}\n"
                "1. 把内容拆解迁移成路飞可执行的用户体验方案：封面、标题、前 15 秒、评论钩子、资料领取、私信承接。\n"
                "2. 按路飞现有服务入口路由：1V1 体验咨询、交个朋友、作品集工作坊、求职辅导群。\n"
                "3. 输出只到草稿/brief，不设计完整模拟面试机器人，不要求自动发布。"
            )
    if task_type in {"interview-debrief", "resume-review", "portfolio-review"}:
        flow = {
            "interview-debrief": "面试复盘：时间线、面试官问题、回答质量、卡点、下次训练动作。",
            "resume-review": "简历点评：目标岗位、经历证据、项目亮点、缺口、可量化改写。",
            "portfolio-review": "作品集点评：叙事、项目价值、设计过程、视觉呈现、追问风险。",
        }[task_type]
        if profile == "lufei-bezos":
            return (
                "执行说明 / Jeff Bezos:\n"
                f"{common}\n"
                f"1. 从微信/会议/CRM 中抽取客户需求与上下文，按 `{flow}` 建立问题清单。\n"
                "2. 标记内部可用信息与客户可见信息，保护隐私。\n"
                "3. 产出 backlog 与需要路飞追问的问题。"
            )
        if profile == "lufei-jobs":
            return (
                "执行说明 / Steve Jobs:\n"
                f"{common}\n"
                f"1. 设计 `{flow}` 的可复用交付模板。\n"
                "2. 输出客户看得懂、路飞能快速审核的结构化建议。\n"
                "3. 保留路飞最终专业判断，不替代高价值 1V1。"
            )
        if profile == "lufei-page":
            return (
                "执行说明 / Larry Page:\n"
                f"{common}\n"
                "1. 查询 llm-wiki、腾讯会议 raw、课程/咨询资料和相似案例。\n"
                "2. 每条结论尽量给来源路径；证据不足标注 confidence: low。\n"
                "3. 不把单个学员隐私沉淀到公开 concepts。"
            )
    if task_type == "feedback-backlog":
        if profile == "lufei-bezos":
            return (
                "执行说明 / Jeff Bezos:\n"
                f"{common}\n"
                "1. 扫描评论、微信反馈、CRM 和会议摘要，聚类反复出现的问题。\n"
                "2. 输出 `queries/feedback-backlog-<date>.md` 的候选条目：需求、证据、建议动作、优先级。\n"
                "3. 区分：内容选题、服务改进、客户跟进、未来 SKU 线索。"
            )
    return (
        f"执行说明 / {role_by_profile(profile).codename}:\n"
        f"{common}\n"
        "请按你的 SOUL.md 职责完成该卡片，并在完成时写清引用、置信度、产物路径和剩余风险。"
    )


def verifier_instruction(task_type: str, *, source: str | None = None, wiki_path: Path = DEFAULT_WIKI_PATH) -> str:
    return (
        "执行说明 / Sam Altman 质量门:\n"
        f"Source: {source or '(manual)'}\n"
        f"Wiki: {wiki_path}\n\n"
        "验收项：\n"
        f"{_acceptance_text(task_type)}\n\n"
        "必须检查：\n"
        "- 事实是否有 raw/wiki/CRM/会议/用户输入来源。\n"
        "- 是否编造点赞、收藏、评论、成交等数据。\n"
        "- 是否符合路飞 persona 与小红书账号现有服务边界。\n"
        "- 是否泄露学员隐私或把 private 信息写到公开页。\n"
        "通过时完成任务并带 metadata `{\"gate\":\"pass\"}`；不通过时 block 并列出缺口。"
    )


def synthesizer_instruction(task_type: str, *, source: str | None = None, wiki_path: Path = DEFAULT_WIKI_PATH) -> str:
    return (
        "执行说明 / Elon Mask 终局综合:\n"
        f"Source: {source or '(manual)'}\n"
        f"Wiki: {wiki_path}\n\n"
        "在 Sam Altman 通过后，把 worker 结果综合成给路飞/azan看的最终稿：\n"
        "- 本次任务结论和可直接使用的产物路径。\n"
        "- 对应小红书内容：爆款公式、Top 5 选题、1 条逐字稿、评论/私信/店铺承接建议。\n"
        "- 对应咨询/点评：诊断草稿、追问清单、路飞需要人工判断的位置。\n"
        "- 仍然缺的数据和下一步 Kanban 卡片建议。"
    )


def swarm_comment_commands(
    created: dict[str, object],
    task_type: str,
    *,
    source: str | None = None,
    wiki_path: Path = DEFAULT_WIKI_PATH,
) -> list[list[str]]:
    template = TASK_TEMPLATES[task_type]
    worker_ids = [str(task_id) for task_id in created.get("worker_ids", [])]
    worker_profiles = [str(profile) for profile in template["workers"]]  # type: ignore[index]
    commands: list[list[str]] = []
    for task_id, profile in zip(worker_ids, worker_profiles):
        commands.append(
            comment_command(
                task_id,
                worker_instruction(task_type, profile, source=source, wiki_path=wiki_path),
            )
        )
    verifier_id = created.get("verifier_id")
    if verifier_id:
        commands.append(
            comment_command(
                str(verifier_id),
                verifier_instruction(task_type, source=source, wiki_path=wiki_path),
            )
        )
    synthesizer_id = created.get("synthesizer_id")
    if synthesizer_id:
        commands.append(
            comment_command(
                str(synthesizer_id),
                synthesizer_instruction(task_type, source=source, wiki_path=wiki_path),
            )
        )
    return commands


def create_swarm_with_instructions(
    task_type: str,
    *,
    source: str | None = None,
    wiki_path: Path = DEFAULT_WIKI_PATH,
    run_id: str | None = None,
    dry_run: bool = False,
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
) -> dict[str, object]:
    cmd = swarm_command(task_type, source=source, wiki_path=wiki_path, run_id=run_id) + ["--json"]
    if dry_run:
        return {
            "swarm_command": shell_join(cmd),
            "comment_commands": [
                shell_join(command)
                for command in swarm_comment_commands(
                    {
                        "worker_ids": ["<worker-1>", "<worker-2>", "<worker-3>"],
                        "verifier_id": "<verifier>",
                        "synthesizer_id": "<synthesizer>",
                    },
                    task_type,
                    source=source,
                    wiki_path=wiki_path,
                )
            ],
        }
    active_runner = runner or (lambda c: subprocess.run(c, check=True, text=True, capture_output=True))
    proc = active_runner(cmd)
    created = json.loads(proc.stdout)
    comment_results = []
    for command in swarm_comment_commands(created, task_type, source=source, wiki_path=wiki_path):
        comment_proc = active_runner(command)
        comment_results.append(comment_proc.stdout.strip())
    return {
        "created": created,
        "comments_added": len(comment_results),
        "comment_results": comment_results,
    }


def blocked_seed_command(task_type: str, *, source: str | None = None, wiki_path: Path = DEFAULT_WIKI_PATH) -> list[str]:
    template = TASK_TEMPLATES[task_type]
    return [
        "hermes",
        "kanban",
        "create",
        str(template["title"]),
        "--tenant",
        "lufei",
        "--assignee",
        "lufei-ceo",
        "--created-by",
        "lufei-ceo",
        "--initial-status",
        "blocked",
        "--idempotency-key",
        f"lufei:seed:{task_type}:{source or 'manual'}",
        "--body",
        task_body(task_type, source=source, wiki_path=wiki_path),
    ]


def block_command(task_id: str) -> list[str]:
    return [
        "hermes",
        "kanban",
        "block",
        task_id,
        "安全种子任务：仅验证路飞 AI Team Kanban 写入，等人工确认后再 unblock/decompose",
    ]


def run(cmd: list[str], *, dry_run: bool = False) -> subprocess.CompletedProcess[str] | None:
    if dry_run:
        print(shell_join(cmd))
        return None
    return subprocess.run(cmd, check=True, text=True, capture_output=True)


def shell_join(parts: Iterable[str]) -> str:
    import shlex

    return " ".join(shlex.quote(str(part)) for part in parts)


def setup_profiles(*, dry_run: bool = False, force_soul: bool = False) -> dict[str, object]:
    root = profiles_root()
    created: list[str] = []
    updated: list[str] = []
    for role in ROLES:
        profile_dir = root / role.profile
        if not profile_dir.exists():
            cmd = [
                "hermes",
                "profile",
                "create",
                role.profile,
                "--clone",
                "--description",
                role.description,
            ]
            run(cmd, dry_run=dry_run)
            created.append(role.profile)
        else:
            cmd = ["hermes", "profile", "describe", role.profile, "--text", role.description]
            run(cmd, dry_run=dry_run)
            updated.append(role.profile)
        if not dry_run:
            profile_dir.mkdir(parents=True, exist_ok=True)
            soul_path = profile_dir / "SOUL.md"
            if force_soul or not soul_path.exists() or "路飞设计沉思录知识水电站" not in soul_path.read_text(encoding="utf-8", errors="ignore"):
                soul_path.write_text(render_soul(role), encoding="utf-8")
    return {"created": created, "updated": updated, "profiles_root": str(root)}


def doctor(wiki_path: Path, skills_path: Path) -> dict[str, object]:
    root = profiles_root()
    profiles = []
    for role in ROLES:
        profile_dir = root / role.profile
        soul_path = profile_dir / "SOUL.md"
        profiles.append(
            {
                "profile": role.profile,
                "codename": role.codename,
                "exists": profile_dir.exists(),
                "soul_exists": soul_path.exists(),
            }
        )
    required_wiki = [
        wiki_path / "entities" / "lufei.md",
        wiki_path / "persona.md",
        wiki_path / "crm" / "index.md",
        wiki_path / "_derived" / "manifest.json",
    ]
    return {
        "profiles_root": str(root),
        "profiles": profiles,
        "wiki_path": str(wiki_path),
        "wiki_checks": {str(path): path.exists() for path in required_wiki},
        "skills_path": str(skills_path),
        "skills_exists": skills_path.exists(),
        "task_types": sorted(TASK_TEMPLATES),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wiki-path", type=Path, default=DEFAULT_WIKI_PATH)
    parser.add_argument("--skills-path", type=Path, default=DEFAULT_SKILLS_PATH)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="Check profile/wiki/skill readiness")

    p_setup = sub.add_parser("setup-profiles", help="Create/update Lufei AI team profiles")
    p_setup.add_argument("--dry-run", action="store_true")
    p_setup.add_argument("--force-soul", action="store_true", help="Rewrite existing SOUL.md files")

    p_soul = sub.add_parser("render-soul", help="Print one role's SOUL.md")
    p_soul.add_argument("profile")

    p_body = sub.add_parser("render-task", help="Print a Kanban task body")
    p_body.add_argument("task_type", choices=sorted(TASK_TEMPLATES))
    p_body.add_argument("--source", default=None)

    p_swarm = sub.add_parser("render-swarm-command", help="Print the Hermes Kanban swarm command")
    p_swarm.add_argument("task_type", choices=sorted(TASK_TEMPLATES))
    p_swarm.add_argument("--source", default=None)
    p_swarm.add_argument("--run-id", default=None, help="Append a retry/run suffix to the idempotency key")

    p_create_swarm = sub.add_parser("create-swarm", help="Create a Kanban swarm and attach role instructions")
    p_create_swarm.add_argument("task_type", choices=sorted(TASK_TEMPLATES))
    p_create_swarm.add_argument("--source", default=None)
    p_create_swarm.add_argument("--run-id", default=None, help="Append a retry/run suffix to the idempotency key")
    p_create_swarm.add_argument("--dry-run", action="store_true")

    p_seed = sub.add_parser("create-blocked-seed-task", help="Create a safe blocked seed Kanban task")
    p_seed.add_argument("task_type", choices=sorted(TASK_TEMPLATES))
    p_seed.add_argument("--source", default=None)
    p_seed.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)
    if args.cmd == "doctor":
        print(json.dumps(doctor(args.wiki_path, args.skills_path), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "setup-profiles":
        print(json.dumps(setup_profiles(dry_run=args.dry_run, force_soul=args.force_soul), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "render-soul":
        print(render_soul(role_by_profile(args.profile), args.wiki_path))
        return 0
    if args.cmd == "render-task":
        print(task_body(args.task_type, source=args.source, wiki_path=args.wiki_path))
        return 0
    if args.cmd == "render-swarm-command":
        print(
            shell_join(
                swarm_command(
                    args.task_type,
                    source=args.source,
                    wiki_path=args.wiki_path,
                    run_id=args.run_id,
                )
            )
        )
        return 0
    if args.cmd == "create-swarm":
        print(
            json.dumps(
                create_swarm_with_instructions(
                    args.task_type,
                    source=args.source,
                    wiki_path=args.wiki_path,
                    run_id=args.run_id,
                    dry_run=args.dry_run,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.cmd == "create-blocked-seed-task":
        cmd = blocked_seed_command(args.task_type, source=args.source, wiki_path=args.wiki_path)
        proc = run(cmd, dry_run=args.dry_run)
        if proc is not None:
            print(proc.stdout.rstrip())
            if proc.stderr:
                print(proc.stderr.rstrip(), file=sys.stderr)
            task_id = None
            for token in proc.stdout.split():
                if token.startswith("t_"):
                    task_id = token
                    break
            if task_id:
                block_proc = run(block_command(task_id), dry_run=False)
                if block_proc is not None:
                    print(block_proc.stdout.rstrip())
                    if block_proc.stderr:
                        print(block_proc.stderr.rstrip(), file=sys.stderr)
        return 0
    raise AssertionError(args.cmd)


if __name__ == "__main__":
    raise SystemExit(main())
