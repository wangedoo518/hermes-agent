#!/usr/bin/env python3
"""Query Lu Fei's Obsidian CRM manifest and assemble evidence context."""

from __future__ import annotations

import argparse
import json
import os
import re
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
CRM_MANIFEST_PATH = "_derived/lufei-crm-manifest.json"

THEME_ALIASES: dict[str, tuple[str, ...]] = {
    "portfolio": ("作品集", "项目集", "案例", "项目", "portfolio"),
    "resume": ("简历", "履历", "筛选", "resume"),
    "interview": ("面试", "模拟面试", "追问", "答题", "interview"),
    "written_test": ("笔试", "作业", "命题", "written"),
    "web_coding": ("web coding", "前端", "编程", "代码", "coding"),
    "ai_agent": ("ai", "agent", "智能体", "大模型"),
    "uiux": ("ui", "ux", "交互", "视觉", "体验设计", "设计系统"),
    "career": ("求职", "offer", "校招", "社招", "大厂", "跳槽"),
    "product": ("产品", "需求", "mvp", "prd", "商业化"),
    "communication": ("沟通", "表达", "汇报", "话术", "逻辑"),
}

SERVICE_ALIASES: dict[str, tuple[str, ...]] = {
    "coaching": ("辅导", "coaching"),
    "consultation": ("咨询", "consultation"),
    "mock_interview": ("模拟面试", "面试", "mock"),
    "written_test_coaching": ("笔试", "作业"),
    "project_coaching": ("项目辅导",),
    "project_discussion": ("项目讨论", "讨论"),
    "discovery_interview": ("访谈",),
}

RECENT_KEYWORDS = ("最近", "最新", "上次", "近况", "进展", "last", "latest", "recent")


def now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end >= 0:
            return text[end + 4 :].lstrip()
    return text


def read_text(path: Path, *, max_chars: int = 50000) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")[:max_chars]


def normalise(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().lower()


def split_terms(query: str) -> list[str]:
    query = normalise(query)
    terms = [item for item in re.split(r"[\s,，/|:：;；]+", query) if len(item) >= 2]
    # Add known domain keywords from continuous Chinese text, e.g. "吴晓钰最近作品集进展".
    for aliases in (*THEME_ALIASES.values(), *SERVICE_ALIASES.values()):
        for alias in aliases:
            if alias.lower() in query and alias.lower() not in terms:
                terms.append(alias.lower())
    return terms


def infer_theme_filters(query: str, explicit: str | None = None) -> list[str]:
    themes: list[str] = []
    if explicit:
        themes.extend([item.strip() for item in explicit.split(",") if item.strip()])
    lowered = normalise(query)
    for theme, aliases in THEME_ALIASES.items():
        if any(alias.lower() in lowered for alias in aliases) and theme not in themes:
            themes.append(theme)
    return themes


def infer_service_filter(query: str, explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    lowered = normalise(query)
    for service, aliases in SERVICE_ALIASES.items():
        if any(alias.lower() in lowered for alias in aliases):
            return service
    return None


def has_recent_intent(query: str) -> bool:
    lowered = normalise(query)
    return any(keyword in lowered for keyword in RECENT_KEYWORDS)


def find_student_matches(
    students: dict[str, dict[str, Any]],
    *,
    query: str,
    explicit: str | None = None,
) -> list[str]:
    lowered_query = normalise(" ".join([query, explicit or ""]))
    matches: list[str] = []
    if not lowered_query:
        return matches
    for name in students:
        lowered_name = normalise(name)
        if lowered_name and lowered_name in lowered_query:
            matches.append(name)
    if explicit and not matches:
        for name in students:
            if normalise(explicit) in normalise(name) or normalise(name) in normalise(explicit):
                matches.append(name)
    return matches


def meeting_score(
    meeting: dict[str, Any],
    *,
    terms: list[str],
    student_matches: list[str],
    theme_filters: list[str],
    service_filter: str | None,
) -> int:
    score = 0
    title = normalise(meeting.get("title", ""))
    evidence = normalise(meeting.get("evidence_snippet", ""))
    themes = set(meeting.get("themes") or [])
    students = set(meeting.get("students") or [])
    service = meeting.get("service_type")

    if student_matches:
        if students.intersection(student_matches):
            score += 120
        else:
            return -1
    if theme_filters:
        overlap = themes.intersection(theme_filters)
        if overlap:
            score += 30 * len(overlap)
        else:
            return -1
    if service_filter:
        if service == service_filter:
            score += 30
        else:
            return -1

    for term in terms:
        if term in title:
            score += 20
        if term in evidence:
            score += 8
        if term in " ".join(themes):
            score += 12
        if term in " ".join(normalise(s) for s in students):
            score += 20

    # Prefer recent meetings when scores tie. Date format is sortable.
    date = meeting.get("start_time_text") or ""
    if date:
        score += int(date[:4].isdigit()) * 1
    if not terms and not student_matches and not theme_filters and not service_filter:
        score += 1
    return score


def extract_excerpt(text: str, terms: list[str], *, max_len: int = 700) -> str:
    cleaned = strip_frontmatter(text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if not cleaned:
        return ""
    lowered = cleaned.lower()
    idx = -1
    for term in terms:
        idx = lowered.find(term.lower())
        if idx >= 0:
            break
    if idx < 0:
        # Skip headings/frontmatter-ish boilerplate when possible.
        lines = [
            line.strip()
            for line in cleaned.splitlines()
            if line.strip() and not line.startswith("#") and line.strip() not in {"返回", "分享", "另存为", "翻译"}
        ]
        return "\n".join(lines[:8])[:max_len]
    start = max(0, idx - max_len // 3)
    end = min(len(cleaned), start + max_len)
    return cleaned[start:end].strip()


def load_asset_excerpt(
    wiki_path: Path,
    meeting: dict[str, Any],
    terms: list[str],
    *,
    max_len: int = 900,
) -> dict[str, str]:
    smart_path = wiki_path / meeting.get("smart_minutes_path", "")
    transcript_path = wiki_path / meeting.get("transcript_path", "")
    smart = extract_excerpt(read_text(smart_path), terms, max_len=max_len)
    transcript = extract_excerpt(read_text(transcript_path), terms, max_len=max_len)
    return {"smart_minutes_excerpt": smart, "transcript_excerpt": transcript}


def build_context_markdown(
    *,
    query: str,
    student_matches: list[dict[str, Any]],
    meetings: list[dict[str, Any]],
    generated_at: str,
) -> str:
    lines = [
        "# CRM Context",
        "",
        f"- query: {query or '(recent meetings)'}",
        f"- generated_at: {generated_at}",
        f"- matched_students: {len(student_matches)}",
        f"- matched_meetings: {len(meetings)}",
        "",
    ]
    if student_matches:
        lines.extend(["## Matched Students", ""])
        for student in student_matches:
            lines.append(
                "- "
                f"{student['name']} | meetings={student['meeting_count']} | "
                f"latest={student['last_seen']} | page={student['path']} | "
                f"themes={', '.join(student.get('themes', [])[:6])}"
            )
        lines.append("")
    lines.extend(["## Evidence Meetings", ""])
    for index, meeting in enumerate(meetings, start=1):
        lines.extend(
            [
                f"### {index}. {meeting['title']}",
                "",
                f"- date: {meeting.get('start_time_text', '')}",
                f"- students: {', '.join(meeting.get('students', [])) or 'N/A'}",
                f"- service_type: {meeting.get('service_type', 'unknown')}",
                f"- themes: {', '.join(meeting.get('themes', []))}",
                f"- source: {meeting.get('source_path', '')}",
                f"- smart_minutes: {meeting.get('smart_minutes_path', '')}",
                f"- transcript: {meeting.get('transcript_path', '')}",
                "",
                "Evidence:",
                "",
                meeting.get("smart_minutes_excerpt")
                or meeting.get("evidence_snippet")
                or "(no smart minutes excerpt)",
                "",
            ]
        )
        transcript = meeting.get("transcript_excerpt")
        if transcript:
            lines.extend(["Transcript excerpt:", "", transcript, ""])
    return "\n".join(lines).strip() + "\n"


def query_crm_context(
    *,
    wiki_path: Path = DEFAULT_WIKI_PATH,
    query: str,
    student: str | None = None,
    theme: str | None = None,
    service_type: str | None = None,
    max_meetings: int = 8,
    max_chars: int = 12000,
) -> dict[str, Any]:
    wiki_path = wiki_path.expanduser().resolve()
    manifest_path = wiki_path / CRM_MANIFEST_PATH
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"Missing CRM manifest: {manifest_path}. Run tmeet_build_lufei_crm first."
        )
    manifest = read_json(manifest_path)
    query = query or ""
    terms = split_terms(query)
    theme_filters = infer_theme_filters(query, theme)
    service_filter = infer_service_filter(query, service_type)
    student_names = find_student_matches(manifest.get("students", {}), query=query, explicit=student)
    recent_intent = has_recent_intent(query)

    scored: list[tuple[int, str, dict[str, Any]]] = []
    for meeting in manifest.get("meetings", []):
        score = meeting_score(
            meeting,
            terms=terms,
            student_matches=student_names,
            theme_filters=theme_filters,
            service_filter=service_filter,
        )
        if score >= 0:
            scored.append((score, meeting.get("start_time_text") or "", meeting))
    if recent_intent and student_names:
        scored.sort(key=lambda item: (item[1], item[0]), reverse=True)
    else:
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    selected = [meeting.copy() for _, _, meeting in scored[: max(1, min(max_meetings, 30))]]
    for meeting in selected:
        meeting.update(load_asset_excerpt(wiki_path, meeting, terms))

    matched_students = [
        manifest["students"][name]
        for name in student_names
        if name in manifest.get("students", {})
    ]
    if not matched_students and selected:
        seen: set[str] = set()
        for meeting in selected:
            for name in meeting.get("students", []):
                if name in manifest.get("students", {}) and name not in seen:
                    matched_students.append(manifest["students"][name])
                    seen.add(name)
                if len(matched_students) >= 8:
                    break

    generated_at = now_iso()
    context = build_context_markdown(
        query=query,
        student_matches=matched_students,
        meetings=selected,
        generated_at=generated_at,
    )
    if len(context) > max_chars:
        context = context[:max_chars].rstrip() + "\n\n[truncated]\n"

    return {
        "success": True,
        "generated_at": generated_at,
        "wiki_path": str(wiki_path),
        "manifest_path": str(manifest_path),
        "query": query,
        "terms": terms,
        "student_filter": student,
        "student_matches": matched_students,
        "theme_filters": theme_filters,
        "service_filter": service_filter,
        "recent_intent": recent_intent,
        "matched_meeting_count": len(scored),
        "returned_meeting_count": len(selected),
        "meetings": selected,
        "context_markdown": context,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?", default="")
    parser.add_argument("--wiki-path", type=Path, default=DEFAULT_WIKI_PATH)
    parser.add_argument("--student")
    parser.add_argument("--theme")
    parser.add_argument("--service-type")
    parser.add_argument("--max-meetings", type=int, default=8)
    parser.add_argument("--max-chars", type=int, default=12000)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = query_crm_context(
        wiki_path=args.wiki_path,
        query=args.query,
        student=args.student,
        theme=args.theme,
        service_type=args.service_type,
        max_meetings=args.max_meetings,
        max_chars=args.max_chars,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["context_markdown"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
