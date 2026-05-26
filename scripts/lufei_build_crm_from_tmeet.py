#!/usr/bin/env python3
"""Build a lightweight student CRM from Tencent Meeting raw assets.

This is intentionally a file adapter: it reads the deterministic Tencent
Meeting raw/detail manifest, classifies meetings by title and text evidence,
then writes Obsidian-friendly Markdown plus a machine-readable manifest.
It does not infer private facts beyond the meeting evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


DEFAULT_WIKI_PATH = Path(
    os.getenv("HERMES_XHS_WIKI_PATH")
    or os.getenv("HERMES_CREATOR_WIKI_PATH")
    or os.getenv("LUFEI_XHS_WIKI_PATH")
    or os.getenv("XHS_WIKI_PATH")
    or os.getenv("WIKI_PATH")
    or "/Users/champion/Documents/develop/lufei/wiki"
).expanduser()
LATEST_DETAIL_POINTER = "_derived/tencent-meetings-cdp-details-latest.json"
GENERATOR = "lufei_build_crm_from_tmeet.py"


SERVICE_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("作品集点评", "portfolio_review"),
    ("模拟面试", "mock_interview"),
    ("面试辅导", "mock_interview"),
    ("笔试辅导", "written_test_coaching"),
    ("项目辅导", "project_coaching"),
    ("项目讨论", "project_discussion"),
    ("设计辅导", "design_coaching"),
    ("访谈", "discovery_interview"),
    ("讨论", "discussion"),
    ("一对一", "one_on_one"),
    ("1v1", "one_on_one"),
    ("辅导", "coaching"),
    ("咨询", "consultation"),
    ("面试", "mock_interview"),
    ("复盘", "review"),
)

COURSE_KEYWORDS = (
    "课程",
    "训练营",
    "工作坊",
    "公开课",
    "直播课",
    "录播课",
    "答疑课",
    "作业点评",
    "讲义",
    "交互设计基础",
    "服务设计",
    "产品思维",
    "增长设计",
    "设计原则",
    "设计指标",
    "设计求职工作坊",
    "高分表达",
    "高频真题",
    "用研",
    "路飞的分享",
    "web coding",
    "前端",
)

UNKNOWN_TITLE_NOISE = (
    "路飞预定的会议",
    "快速会议",
    "测试",
)

THEME_KEYWORDS: dict[str, tuple[str, ...]] = {
    "portfolio": ("作品集", "项目集", "案例", "项目展示", "showreel"),
    "resume": ("简历", "筛选", "履历", "经历"),
    "interview": ("面试", "模拟面试", "追问", "答题", "自我介绍", "复盘"),
    "written_test": ("笔试", "作业", "命题", "试题"),
    "web_coding": ("web coding", "webcode", "前端", "代码", "编程", "coding"),
    "ai_agent": ("ai", "agent", "智能体", "大模型", "llm", "aigc"),
    "uiux": ("ui", "ux", "交互", "视觉", "体验设计", "设计系统"),
    "career": ("求职", "offer", "校招", "社招", "大厂", "跳槽"),
    "product": ("产品经理", "需求", "mvp", "prd", "商业化"),
    "communication": ("沟通", "表达", "汇报", "话术", "逻辑"),
}


@dataclass
class MeetingAsset:
    record_id: str
    title: str
    start_time_text: str
    end_time_text: str
    category: str
    classification: str
    service_type: str
    students: list[str]
    themes: list[str]
    output_dir: str
    source_path: str
    smart_minutes_path: str
    timeline_path: str
    timeline_json_path: str
    transcript_path: str
    detail_report_path: str
    source_url: str
    meeting_code: str
    duration_ms: str
    smart_minutes_char_count: int
    timeline_item_count: int
    transcript_char_count: int
    evidence_snippet: str
    warnings: list[str] = field(default_factory=list)


def now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def safe_filename(value: str, *, fallback: str = "untitled") -> str:
    value = value.strip().replace("\u3000", " ")
    value = re.sub(r"[\\/:*?\"<>|#^[\\]]+", "-", value)
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-._ ")
    return value or fallback


def rel_path(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end >= 0:
            return text[end + 4 :].lstrip()
    return text


def read_text_if_exists(path: Path, *, limit: int | None = None) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="ignore")
    if limit is not None:
        return text[:limit]
    return text


def compact_text(text: str) -> str:
    text = strip_frontmatter(text)
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line in {"返回", "分享", "另存为", "翻译"}:
            continue
        if line in {"纪要", "时间轴", "逐字稿", "会议总结", "会议待办"}:
            continue
        lines.append(line)
    return "\n".join(lines)


def evidence_snippet(smart_minutes: str, transcript: str, *, max_len: int = 260) -> str:
    candidates = compact_text(smart_minutes).splitlines()
    for line in candidates:
        if len(line) >= 30 and not re.match(r"^\d+[、.]", line):
            return line[:max_len]
    fallback = compact_text(transcript).replace("\n", " ")
    return fallback[:max_len]


def normalize_title(title: str) -> str:
    title = (title or "").strip()
    title = re.sub(r"^转写[_-]?", "", title)
    title = re.sub(r"\s+", " ", title)
    return title


def infer_classification(title: str, raw_category: str = "") -> tuple[str, str, list[str]]:
    normalized = normalize_title(title)
    lowered = normalized.lower()
    if any(noise in normalized for noise in UNKNOWN_TITLE_NOISE):
        return "unknown", "unknown", []

    for suffix, service_type in SERVICE_SUFFIXES:
        if normalized.endswith(suffix):
            base = normalized[: -len(suffix)].strip(" -_·：:，,、")
            students = split_student_names(base)
            if students:
                return "student", service_type, students

    if any(keyword.lower() in lowered for keyword in COURSE_KEYWORDS):
        return "course", "course", []

    # Bare-name titles are common in the historical meeting record list.
    # Keep them as low-specificity student touchpoints unless a course/noise
    # keyword already matched above.
    students = split_student_names(normalized)
    if students and len(normalized) <= 24:
        return "student", "touchpoint", students

    return "unknown", "unknown", []


def split_student_names(base: str) -> list[str]:
    base = normalize_title(base)
    base = base.strip(" -_·：:，,、")
    if not base:
        return []
    base = re.sub(r"^(南京|北京|上海|深圳|杭州|广州|成都|武汉|西安|苏州)", "", base).strip()
    if not base:
        return []

    separators = r"[、，,;/&＋+和]"
    if re.search(separators, base):
        parts = [p.strip() for p in re.split(separators, base) if p.strip()]
        return dedupe(parts)

    space_parts = [p.strip() for p in base.split() if p.strip()]
    if len(space_parts) > 1 and all(re.fullmatch(r"[\u4e00-\u9fff]{2,4}", p) for p in space_parts):
        return dedupe(space_parts)

    # Keep Latin names with spaces as one person, e.g. "Ruoling Xu".
    return [base]


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            output.append(value)
    return output


def infer_themes(text: str) -> list[str]:
    lowered = text.lower()
    scores: Counter[str] = Counter()
    for theme, keywords in THEME_KEYWORDS.items():
        for keyword in keywords:
            count = lowered.count(keyword.lower())
            if count:
                scores[theme] += count
    return [theme for theme, _ in scores.most_common(6)]


def resolve_detail_manifest(wiki_path: Path, detail_manifest: Path | None) -> Path:
    if detail_manifest:
        return detail_manifest
    pointer_path = wiki_path / LATEST_DETAIL_POINTER
    if not pointer_path.exists():
        raise FileNotFoundError(
            f"Missing {pointer_path}. Run tmeet_capture_record_details_cdp first."
        )
    pointer = read_json(pointer_path)
    output_root = pointer.get("output_root") or pointer.get("latest_root")
    if not output_root:
        raise ValueError(f"No output_root in {pointer_path}")
    return Path(output_root) / "detail_manifest.json"


def load_meeting_assets(wiki_path: Path, detail_manifest: Path) -> list[MeetingAsset]:
    manifest = read_json(detail_manifest)
    assets: list[MeetingAsset] = []
    for report in manifest.get("reports", []):
        record = report.get("record") or {}
        output_dir = Path(report.get("output_dir") or "")
        if not output_dir:
            continue
        detail_report_path = output_dir / "detail_report.json"
        detail = read_json(detail_report_path) if detail_report_path.exists() else report
        record = detail.get("record") or record
        title = normalize_title(record.get("title") or output_dir.name)
        raw_category = record.get("category") or "unknown"
        classification, service_type, students = infer_classification(title, raw_category)

        source_path = output_dir / "source.md"
        smart_minutes_path = output_dir / "smart_minutes.md"
        timeline_path = output_dir / "timeline.md"
        timeline_json_path = output_dir / "timeline.json"
        transcript_path = output_dir / "transcript.md"

        smart_minutes_text = read_text_if_exists(smart_minutes_path, limit=20000)
        transcript_text = read_text_if_exists(transcript_path, limit=20000)
        theme_source = "\n".join([title, smart_minutes_text, transcript_text])
        themes = infer_themes(theme_source)

        source_url = (
            detail.get("final_url")
            or detail.get("detail_url")
            or record.get("share_url")
            or ""
        )
        assets.append(
            MeetingAsset(
                record_id=str(record.get("record_id") or output_dir.name),
                title=title,
                start_time_text=str(record.get("start_time_text") or ""),
                end_time_text=str(record.get("end_time_text") or ""),
                category=str(raw_category),
                classification=classification,
                service_type=service_type,
                students=students,
                themes=themes,
                output_dir=rel_path(output_dir, wiki_path),
                source_path=rel_path(source_path, wiki_path),
                smart_minutes_path=rel_path(smart_minutes_path, wiki_path),
                timeline_path=rel_path(timeline_path, wiki_path),
                timeline_json_path=rel_path(timeline_json_path, wiki_path),
                transcript_path=rel_path(transcript_path, wiki_path),
                detail_report_path=rel_path(detail_report_path, wiki_path),
                source_url=source_url,
                meeting_code=str(record.get("meeting_code") or ""),
                duration_ms=str(record.get("duration_ms") or ""),
                smart_minutes_char_count=int(detail.get("smart_minutes_char_count") or 0),
                timeline_item_count=int(detail.get("timeline_item_count") or 0),
                transcript_char_count=int(detail.get("transcript_char_count") or 0),
                evidence_snippet=evidence_snippet(smart_minutes_text, transcript_text),
                warnings=list(detail.get("warnings") or []),
            )
        )
    return assets


def clean_generated_markdown(directory: Path) -> None:
    if not directory.exists():
        return
    for path in directory.glob("*.md"):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if f'generated_by: "{GENERATOR}"' in text:
            path.unlink()


def yaml_list(values: list[str]) -> str:
    if not values:
        return "[]"
    escaped = [json.dumps(value, ensure_ascii=False) for value in values]
    return "[" + ", ".join(escaped) + "]"


def yaml_scalar(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def obsidian_link(path: str, label: str | None = None) -> str:
    clean = path.removesuffix(".md")
    return f"[[{clean}|{label}]]" if label else f"[[{clean}]]"


def student_slug(student: str) -> str:
    digest = hashlib.sha1(student.encode("utf-8")).hexdigest()[:6]
    return f"{safe_filename(student)}-{digest}"


def build_student_pages(
    *,
    wiki_path: Path,
    assets: list[MeetingAsset],
    generated_at: str,
) -> dict[str, dict[str, Any]]:
    students_dir = wiki_path / "crm" / "students"
    students_dir.mkdir(parents=True, exist_ok=True)
    clean_generated_markdown(students_dir)

    grouped: dict[str, list[MeetingAsset]] = defaultdict(list)
    for asset in assets:
        for student in asset.students:
            grouped[student].append(asset)

    manifest_students: dict[str, dict[str, Any]] = {}
    for student, meetings in sorted(
        grouped.items(), key=lambda item: (-len(item[1]), item[0])
    ):
        meetings.sort(key=lambda item: item.start_time_text, reverse=True)
        sources = dedupe([m.source_path for m in meetings if m.source_path])
        service_types = [name for name, _ in Counter(m.service_type for m in meetings).most_common()]
        themes = [name for name, _ in Counter(t for m in meetings for t in m.themes).most_common(8)]
        slug = student_slug(student)
        path = students_dir / f"{slug}.md"
        latest = meetings[0]
        first_seen = meetings[-1].start_time_text
        last_seen = latest.start_time_text
        body = [
            "---",
            f"title: {yaml_scalar(student)}",
            'type: "crm/student"',
            f'generated_by: "{GENERATOR}"',
            f"created: {yaml_scalar(generated_at)}",
            f"updated: {yaml_scalar(generated_at)}",
            f"student: {yaml_scalar(student)}",
            f"meeting_count: {len(meetings)}",
            f"first_seen: {yaml_scalar(first_seen)}",
            f"last_seen: {yaml_scalar(last_seen)}",
            f"service_types: {yaml_list(service_types)}",
            f"themes: {yaml_list(themes)}",
            f"sources: {yaml_list(sources)}",
            "---",
            "",
            f"# {student}",
            "",
            "## Snapshot",
            "",
            f"- 会议数：{len(meetings)}",
            f"- 首次出现：{first_seen or 'unknown'}",
            f"- 最近一次：{last_seen or 'unknown'}",
            f"- 服务类型：{', '.join(service_types) if service_types else 'unknown'}",
            f"- 主题标签：{', '.join(themes) if themes else 'unknown'}",
            f"- 最近会议：{obsidian_link(latest.source_path, latest.title)}",
            "",
            "## Meeting Timeline",
            "",
            "| Date | Type | Title | Text Assets | Source |",
            "| --- | --- | --- | --- | --- |",
        ]
        for meeting in meetings:
            assets_cell = " / ".join(
                [
                    obsidian_link(meeting.smart_minutes_path, "纪要"),
                    obsidian_link(meeting.timeline_path, "时间轴"),
                    obsidian_link(meeting.transcript_path, "逐字稿"),
                ]
            )
            body.append(
                "| "
                + " | ".join(
                    [
                        meeting.start_time_text or "",
                        meeting.service_type,
                        escape_table(meeting.title),
                        assets_cell,
                        obsidian_link(meeting.source_path, "source"),
                    ]
                )
                + " |"
            )
        body.extend(["", "## Evidence Notes", ""])
        for meeting in meetings[:8]:
            snippet = meeting.evidence_snippet or "未提取到可读摘要。"
            body.append(
                f"- {meeting.start_time_text} · {meeting.title}：{snippet} "
                f"^[{meeting.source_path}]"
            )
        body.append("")
        path.write_text("\n".join(body), encoding="utf-8")
        manifest_students[student] = {
            "name": student,
            "slug": slug,
            "path": rel_path(path, wiki_path),
            "meeting_count": len(meetings),
            "first_seen": first_seen,
            "last_seen": last_seen,
            "service_types": service_types,
            "themes": themes,
            "sources": sources,
            "latest_meeting": {
                "title": latest.title,
                "start_time_text": latest.start_time_text,
                "source_path": latest.source_path,
            },
        }
    return manifest_students


def escape_table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def write_course_index(wiki_path: Path, assets: list[MeetingAsset], generated_at: str) -> dict[str, Any]:
    courses = [asset for asset in assets if asset.classification == "course"]
    courses_dir = wiki_path / "crm" / "courses"
    courses_dir.mkdir(parents=True, exist_ok=True)
    path = courses_dir / "index.md"
    theme_counts = Counter(theme for asset in courses for theme in asset.themes)
    body = [
        "---",
        'title: "Course Meeting Index"',
        'type: "crm/course-index"',
        f'generated_by: "{GENERATOR}"',
        f"created: {yaml_scalar(generated_at)}",
        f"updated: {yaml_scalar(generated_at)}",
        f"meeting_count: {len(courses)}",
        f"themes: {yaml_list([name for name, _ in theme_counts.most_common(12)])}",
        "---",
        "",
        "# Course Meeting Index",
        "",
        f"- 课程/内容类会议：{len(courses)}",
        f"- 高频主题：{', '.join(name for name, _ in theme_counts.most_common(12)) or 'unknown'}",
        "",
        "| Date | Title | Themes | Text Assets | Source |",
        "| --- | --- | --- | --- | --- |",
    ]
    for asset in sorted(courses, key=lambda item: item.start_time_text, reverse=True):
        assets_cell = " / ".join(
            [
                obsidian_link(asset.smart_minutes_path, "纪要"),
                obsidian_link(asset.timeline_path, "时间轴"),
                obsidian_link(asset.transcript_path, "逐字稿"),
            ]
        )
        body.append(
            "| "
            + " | ".join(
                [
                    asset.start_time_text,
                    escape_table(asset.title),
                    ", ".join(asset.themes),
                    assets_cell,
                    obsidian_link(asset.source_path, "source"),
                ]
            )
            + " |"
        )
    body.append("")
    path.write_text("\n".join(body), encoding="utf-8")
    return {
        "count": len(courses),
        "path": rel_path(path, wiki_path),
        "themes": dict(theme_counts.most_common()),
    }


def write_meeting_assets_index(
    wiki_path: Path, assets: list[MeetingAsset], generated_at: str
) -> str:
    path = wiki_path / "crm" / "meeting-assets.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = [
        "---",
        'title: "Tencent Meeting Asset Index"',
        'type: "crm/meeting-assets"',
        f'generated_by: "{GENERATOR}"',
        f"created: {yaml_scalar(generated_at)}",
        f"updated: {yaml_scalar(generated_at)}",
        f"meeting_count: {len(assets)}",
        "---",
        "",
        "# Tencent Meeting Asset Index",
        "",
        "| Date | Class | People | Title | Text Counts | Source |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for asset in sorted(assets, key=lambda item: item.start_time_text, reverse=True):
        counts = (
            f"纪要 {asset.smart_minutes_char_count} / "
            f"时间轴 {asset.timeline_item_count} / "
            f"逐字稿 {asset.transcript_char_count}"
        )
        body.append(
            "| "
            + " | ".join(
                [
                    asset.start_time_text,
                    asset.classification,
                    escape_table(", ".join(asset.students)),
                    escape_table(asset.title),
                    counts,
                    obsidian_link(asset.source_path, "source"),
                ]
            )
            + " |"
        )
    body.append("")
    path.write_text("\n".join(body), encoding="utf-8")
    return rel_path(path, wiki_path)


def write_crm_index(
    *,
    wiki_path: Path,
    assets: list[MeetingAsset],
    students: dict[str, dict[str, Any]],
    course_index: dict[str, Any],
    meeting_assets_path: str,
    generated_at: str,
    detail_manifest: Path,
) -> str:
    path = wiki_path / "crm" / "index.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    classification_counts = Counter(asset.classification for asset in assets)
    service_counts = Counter(asset.service_type for asset in assets if asset.classification == "student")
    theme_counts = Counter(theme for asset in assets for theme in asset.themes)
    missing_timeline = sum(1 for asset in assets if asset.timeline_item_count == 0)
    missing_transcript = sum(1 for asset in assets if asset.transcript_char_count == 0)
    missing_minutes = sum(1 for asset in assets if asset.smart_minutes_char_count == 0)
    top_students = sorted(
        students.values(), key=lambda item: (-item["meeting_count"], item["name"])
    )[:20]
    body = [
        "---",
        'title: "路飞学员 CRM"',
        'type: "crm/index"',
        f'generated_by: "{GENERATOR}"',
        f"created: {yaml_scalar(generated_at)}",
        f"updated: {yaml_scalar(generated_at)}",
        f"source_manifest: {yaml_scalar(rel_path(detail_manifest, wiki_path))}",
        f"meeting_count: {len(assets)}",
        f"student_count: {len(students)}",
        f"classification_counts: {json.dumps(dict(classification_counts), ensure_ascii=False)}",
        "---",
        "",
        "# 路飞学员 CRM",
        "",
        "## Summary",
        "",
        f"- 会议总数：{len(assets)}",
        f"- 学员数：{len(students)}",
        f"- 课程/内容类会议：{course_index['count']}",
        f"- 未归类会议：{classification_counts.get('unknown', 0)}",
        f"- 缺时间轴：{missing_timeline}",
        f"- 缺逐字稿：{missing_transcript}",
        f"- 缺纪要：{missing_minutes}",
        f"- 全量会议资产：{obsidian_link(meeting_assets_path, 'meeting-assets')}",
        f"- 课程会议索引：{obsidian_link(course_index['path'], 'courses/index')}",
        "",
        "## Service Mix",
        "",
    ]
    for name, count in service_counts.most_common():
        body.append(f"- {name}: {count}")
    body.extend(["", "## Top Students", "", "| Student | Meetings | Latest | Themes | Page |", "| --- | ---: | --- | --- | --- |"])
    for student in top_students:
        body.append(
            "| "
            + " | ".join(
                [
                    escape_table(student["name"]),
                    str(student["meeting_count"]),
                    student["last_seen"],
                    escape_table(", ".join(student["themes"][:6])),
                    obsidian_link(student["path"], "page"),
                ]
            )
            + " |"
        )
    body.extend(["", "## High-Frequency Themes", ""])
    for name, count in theme_counts.most_common(20):
        body.append(f"- {name}: {count}")
    body.append("")
    path.write_text("\n".join(body), encoding="utf-8")
    return rel_path(path, wiki_path)


def serialize_asset(asset: MeetingAsset) -> dict[str, Any]:
    return {
        "record_id": asset.record_id,
        "title": asset.title,
        "start_time_text": asset.start_time_text,
        "end_time_text": asset.end_time_text,
        "category": asset.category,
        "classification": asset.classification,
        "service_type": asset.service_type,
        "students": asset.students,
        "themes": asset.themes,
        "output_dir": asset.output_dir,
        "source_path": asset.source_path,
        "smart_minutes_path": asset.smart_minutes_path,
        "timeline_path": asset.timeline_path,
        "timeline_json_path": asset.timeline_json_path,
        "transcript_path": asset.transcript_path,
        "detail_report_path": asset.detail_report_path,
        "source_url": asset.source_url,
        "meeting_code": asset.meeting_code,
        "duration_ms": asset.duration_ms,
        "smart_minutes_char_count": asset.smart_minutes_char_count,
        "timeline_item_count": asset.timeline_item_count,
        "transcript_char_count": asset.transcript_char_count,
        "evidence_snippet": asset.evidence_snippet,
        "warnings": asset.warnings,
    }


def build_crm_from_tmeet(
    *,
    wiki_path: Path = DEFAULT_WIKI_PATH,
    detail_manifest: Path | None = None,
) -> dict[str, Any]:
    wiki_path = wiki_path.expanduser().resolve()
    detail_manifest = resolve_detail_manifest(wiki_path, detail_manifest)
    if not detail_manifest.exists():
        raise FileNotFoundError(f"Missing detail manifest: {detail_manifest}")
    generated_at = now_iso()
    assets = load_meeting_assets(wiki_path, detail_manifest)
    students = build_student_pages(
        wiki_path=wiki_path,
        assets=[asset for asset in assets if asset.classification == "student"],
        generated_at=generated_at,
    )
    course_index = write_course_index(wiki_path, assets, generated_at)
    meeting_assets_path = write_meeting_assets_index(wiki_path, assets, generated_at)
    crm_index_path = write_crm_index(
        wiki_path=wiki_path,
        assets=assets,
        students=students,
        course_index=course_index,
        meeting_assets_path=meeting_assets_path,
        generated_at=generated_at,
        detail_manifest=detail_manifest,
    )
    classification_counts = Counter(asset.classification for asset in assets)
    service_counts = Counter(asset.service_type for asset in assets if asset.classification == "student")
    theme_counts = Counter(theme for asset in assets for theme in asset.themes)
    manifest = {
        "success": True,
        "generated_at": generated_at,
        "wiki_path": str(wiki_path),
        "source_detail_manifest": rel_path(detail_manifest, wiki_path),
        "crm_index_path": crm_index_path,
        "meeting_assets_path": meeting_assets_path,
        "course_index_path": course_index["path"],
        "meeting_count": len(assets),
        "student_count": len(students),
        "course_count": course_index["count"],
        "classification_counts": dict(classification_counts),
        "service_counts": dict(service_counts),
        "theme_counts": dict(theme_counts.most_common()),
        "missing_text_counts": {
            "smart_minutes": sum(1 for asset in assets if asset.smart_minutes_char_count == 0),
            "timeline": sum(1 for asset in assets if asset.timeline_item_count == 0),
            "transcript": sum(1 for asset in assets if asset.transcript_char_count == 0),
        },
        "students": students,
        "courses": course_index,
        "meetings": [serialize_asset(asset) for asset in assets],
    }
    manifest_path = wiki_path / "_derived" / "lufei-crm-manifest.json"
    write_json(manifest_path, manifest)
    manifest["manifest_path"] = rel_path(manifest_path, wiki_path)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wiki-path", type=Path, default=DEFAULT_WIKI_PATH)
    parser.add_argument("--detail-manifest", type=Path)
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a short summary.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = build_crm_from_tmeet(
        wiki_path=args.wiki_path,
        detail_manifest=args.detail_manifest,
    )
    if args.json:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    else:
        print(f"CRM index: {manifest['crm_index_path']}")
        print(f"Meeting assets: {manifest['meeting_assets_path']}")
        print(f"Manifest: {manifest['manifest_path']}")
        print(
            "Counts: "
            f"{manifest['meeting_count']} meetings, "
            f"{manifest['student_count']} students, "
            f"{manifest['course_count']} course meetings"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
