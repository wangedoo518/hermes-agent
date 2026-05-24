#!/usr/bin/env python3
"""Ingest a Youdao shared notebook of XHS creation scripts into llm-wiki raw/xhs.

The official Youdao CLI is designed for authenticated notes. Public shared
notebooks expose enough read-only JSON endpoints, so this adapter keeps the
ingestion deterministic and avoids requiring a Youdao API key.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import html
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


DEFAULT_WIKI_PATH = Path(
    "/Users/champion/Documents/develop/Wiki/ClaudeWiki/lufei-xhs-wiki"
)
DEFAULT_SHARE_URL = (
    "https://share.note.youdao.com/ynoteshare/index.html"
    "?id=4c03598369e07c3985859bcaaa8fb1e0&type=notebook&_time=1779523596689"
)
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


@dataclass
class ExistingXhsNote:
    note_id: str
    title: str
    note_type: str
    path: Path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def extract_share_id(url_or_id: str) -> str:
    if re.fullmatch(r"[0-9a-fA-F]{32}", url_or_id):
        return url_or_id
    match = re.search(r"[?&]id=([0-9a-fA-F]{32})", url_or_id)
    if not match:
        raise ValueError(f"Cannot find Youdao share id in: {url_or_id}")
    return match.group(1)


def safe_filename(value: str, fallback: str = "untitled") -> str:
    value = value.strip().replace("\u3000", " ")
    value = re.sub(r"[\\/:*?\"<>|#^[\\]]+", "-", value)
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-._ ")
    return value or fallback


def rel(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def normalise_title(value: str) -> str:
    value = html.unescape(value or "").lower()
    value = re.sub(r"\.note$", "", value)
    value = re.sub(r"^[0-9]{1,3}\s+", "", value)
    value = re.sub(r"[\s　《》“”\"'：:，,。！!？?、|/\\()（）\\[\\]【】🔥🎯💰❗️]+", "", value)
    return value


def title_similarity(left: str, right: str) -> float:
    left_norm = normalise_title(left)
    right_norm = normalise_title(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    if left_norm in right_norm or right_norm in left_norm:
        shorter = min(len(left_norm), len(right_norm))
        longer = max(len(left_norm), len(right_norm))
        return 0.82 + 0.18 * (shorter / max(longer, 1))
    seq = difflib.SequenceMatcher(None, left_norm, right_norm).ratio()
    left_tokens = set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", left_norm))
    right_tokens = set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", right_norm))
    overlap = 0.0
    if left_tokens and right_tokens:
        overlap = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    return max(seq, overlap)


def load_existing_xhs_notes(wiki_path: Path) -> list[ExistingXhsNote]:
    notes: list[ExistingXhsNote] = []
    notes_root = wiki_path / "raw" / "xhs" / "notes"
    for note_dir in sorted(notes_root.iterdir()) if notes_root.exists() else []:
        if not note_dir.is_dir():
            continue
        note_json = note_dir / "note.json"
        if not note_json.exists():
            continue
        try:
            data = read_json(note_json)
        except json.JSONDecodeError:
            continue
        title = data.get("title") or data.get("display_title") or ""
        notes.append(
            ExistingXhsNote(
                note_id=note_dir.name,
                title=title,
                note_type=data.get("note_type") or data.get("type") or "",
                path=note_dir,
            )
        )
    return notes


def parse_youdao_content(content: str) -> list[str]:
    if not content:
        return []
    if content.lstrip().startswith("<div"):
        text = re.sub(r"<br\\s*/?>", "\n", content)
        text = re.sub(r"</div\\s*>", "\n", text)
        text = re.sub(r"<[^>]+>", "", text)
        return [line.strip() for line in html.unescape(text).splitlines()]

    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        text = re.sub(r"<[^>]+>", "\n", content)
        return [line.strip() for line in html.unescape(text).splitlines()]

    lines: list[str] = []
    ns = {"n": "http://note.youdao.com"}
    for node in root.findall(".//n:text", ns):
        lines.append((node.text or "").strip())
    return lines


def compact_lines(lines: list[str]) -> list[str]:
    output: list[str] = []
    blank = False
    for line in lines:
        line = html.unescape(line).replace("\xa0", " ").strip()
        if not line:
            if not blank and output:
                output.append("")
            blank = True
            continue
        output.append(line)
        blank = False
    while output and not output[-1]:
        output.pop()
    return output


def extract_field(lines: list[str], field: str) -> str:
    pattern = re.compile(rf"^{re.escape(field)}\s*[:：]\s*(.*)$", re.I)
    for index, line in enumerate(lines):
        match = pattern.match(line.strip())
        if not match:
            continue
        value = match.group(1).strip()
        if value:
            return value
        for next_line in lines[index + 1 : index + 4]:
            if next_line.strip():
                return next_line.strip()
    return ""


def extract_script_text(lines: list[str]) -> str:
    for label in ("逐字稿", "文案", "正文"):
        for index, line in enumerate(lines):
            if re.match(rf"^{label}\s*[:：]?\s*$", line.strip()):
                return "\n".join(lines[index + 1 :]).strip()
            inline = re.match(rf"^{label}\s*[:：]\s*(.+)$", line.strip())
            if inline:
                rest = [inline.group(1).strip(), *lines[index + 1 :]]
                return "\n".join(rest).strip()
    return "\n".join(lines).strip()


def infer_system_title(lines: list[str], note_title: str) -> str:
    for field in ("系统标题", "标题"):
        value = extract_field(lines, field)
        if value:
            return value
    stripped_note = re.sub(r"\.note$", "", note_title).strip()
    stripped_note = re.sub(r"^[0-9]{1,3}\s+", "", stripped_note).strip()
    return stripped_note


def best_match(
    system_title: str,
    notebook_title: str,
    existing: list[ExistingXhsNote],
) -> tuple[ExistingXhsNote | None, float]:
    candidates = [system_title, notebook_title]
    normalized_candidates = [normalise_title(item) for item in candidates if item]
    if not normalized_candidates:
        return None, 0.0
    for candidate in normalized_candidates:
        for note in existing:
            if normalise_title(note.title) == candidate:
                return note, 1.0
    for candidate in normalized_candidates:
        if len(candidate) < 8:
            continue
        for note in existing:
            note_norm = normalise_title(note.title)
            if candidate in note_norm or note_norm in candidate:
                return note, title_similarity(candidate, note.title)

    best: tuple[ExistingXhsNote | None, float] = (None, 0.0)
    for candidate in candidates:
        if len(normalise_title(candidate)) < 8:
            continue
        for note in existing:
            score = title_similarity(candidate, note.title)
            if score > best[1]:
                best = (note, score)
    if best[0] and best[1] >= 0.72:
        return best
    return None, best[1]


def fetch_notebook(session: requests.Session, share_id: str) -> list[Any]:
    url = f"https://share.note.youdao.com/yws/public/notebook/{share_id}"
    response = session.get(url, timeout=30)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list) or len(data) < 3 or not isinstance(data[2], list):
        raise ValueError(f"Unexpected Youdao notebook response from {url}")
    return data


def fetch_note(session: requests.Session, share_id: str, file_id: str) -> dict[str, Any]:
    url = f"https://share.note.youdao.com/yws/api/note/{share_id}/{file_id}"
    response = session.get(
        url,
        params={"sev": "j1", "editorType": "1", "unloginId": "hermes-xhs-ingest"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def render_youdao_note_md(
    *,
    note: dict[str, Any],
    lines: list[str],
    system_title: str,
    script_text: str,
    share_id: str,
    source_url: str,
    file_id: str,
    matched: ExistingXhsNote | None,
    fetched_at: str,
    digest: str,
) -> str:
    matched_path = rel(matched.path, DEFAULT_WIKI_PATH) if matched else ""
    matched_note_id = matched.note_id if matched else ""
    title = note.get("tl") or system_title or file_id
    body = [
        "---",
        f'title: "{title}"',
        'type: "raw/xhs-youdao-script"',
        'platform: "youdao"',
        'source_kind: "shared_notebook_note"',
        f'source_url: "{source_url}"',
        f'share_id: "{share_id}"',
        f'file_id: "{file_id}"',
        f'fetched_at: "{fetched_at}"',
        f'sha256: "{digest}"',
        f'system_title: "{system_title}"',
        f'matched_xhs_note_id: "{matched_note_id}"',
        f'matched_xhs_path: "{matched_path}"',
        "tags:",
        '  - "xhs"',
        '  - "youdao-script"',
        "---",
        "",
        f"# {title}",
        "",
        "## Match",
        "",
        f"- system_title: {system_title or ''}",
        f"- matched_xhs_note_id: {matched_note_id or 'unmatched'}",
        f"- matched_xhs_path: `{matched_path}`" if matched_path else "- matched_xhs_path: ",
        "",
        "## Script",
        "",
        script_text or "_No script text extracted._",
        "",
        "## Full Note Text",
        "",
        "\n".join(lines).strip(),
        "",
    ]
    return "\n".join(body)


def render_creative_script_md(
    *,
    matched: ExistingXhsNote,
    system_title: str,
    source_md_rel: str,
    script_text: str,
    share_url: str,
    file_id: str,
    fetched_at: str,
    digest: str,
) -> str:
    return "\n".join(
        [
            "---",
            f'title: "{matched.title} - Creative Script"',
            'type: "raw/xhs-creative-script"',
            'platform: "youdao"',
            f'note_id: "{matched.note_id}"',
            f'note_title: "{matched.title}"',
            f'source_url: "{share_url}"',
            f'file_id: "{file_id}"',
            f'fetched_at: "{fetched_at}"',
            f'sha256: "{digest}"',
            f'system_title: "{system_title}"',
            "sources:",
            f'  - "{source_md_rel}"',
            "tags:",
            '  - "xhs"',
            '  - "creative-script"',
            "---",
            "",
            f"# {matched.title} - Creative Script",
            "",
            f"- Source: [[{source_md_rel.removesuffix('.md')}|Youdao script]]",
            f"- File ID: `{file_id}`",
            "",
            "## Script",
            "",
            script_text or "_No script text extracted._",
            "",
        ]
    )


def ingest_youdao_xhs_scripts(
    *,
    share_url: str = DEFAULT_SHARE_URL,
    wiki_path: Path = DEFAULT_WIKI_PATH,
) -> dict[str, Any]:
    wiki_path = wiki_path.expanduser().resolve()
    share_id = extract_share_id(share_url)
    fetched_at = now_iso()
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Referer": share_url,
        }
    )
    notebook = fetch_notebook(session, share_id)
    existing_notes = load_existing_xhs_notes(wiki_path)
    output_root = wiki_path / "raw" / "xhs" / "youdao-scripts" / share_id
    notes_root = output_root / "notes"
    raw_notebook_path = output_root / "notebook.json"
    write_json(raw_notebook_path, notebook)

    records: list[dict[str, Any]] = []
    for index, item in enumerate(notebook[2], start=1):
        file_id = str(item.get("p", "")).split("/")[-1]
        if not file_id:
            continue
        detail = fetch_note(session, share_id, file_id)
        content = detail.get("content") or ""
        lines = compact_lines(parse_youdao_content(content))
        system_title = infer_system_title(lines, detail.get("tl") or item.get("tl") or "")
        script_text = extract_script_text(lines)
        source_url = f"https://share.note.youdao.com/ynoteshare/index.html?id={share_id}&type=notebook#/{file_id}"
        digest_payload = json.dumps(
            {"detail": detail, "lines": lines, "script_text": script_text},
            ensure_ascii=False,
            sort_keys=True,
        )
        digest = sha256_text(digest_payload)
        matched, match_score = best_match(
            system_title,
            detail.get("tl") or item.get("tl") or "",
            existing_notes,
        )

        slug = f"{index:02d}-{safe_filename(detail.get('tl') or item.get('tl') or file_id)}"
        note_dir = notes_root / slug
        raw_json_path = note_dir / "youdao_note.json"
        source_md_path = note_dir / "source.md"
        write_json(raw_json_path, detail)
        source_md = render_youdao_note_md(
            note=detail,
            lines=lines,
            system_title=system_title,
            script_text=script_text,
            share_id=share_id,
            source_url=source_url,
            file_id=file_id,
            matched=matched,
            fetched_at=fetched_at,
            digest=digest,
        )
        write_text(source_md_path, source_md)
        source_md_rel = rel(source_md_path, wiki_path)

        creative_script_rel = ""
        if matched:
            creative_script_path = matched.path / "creative_script.md"
            write_text(
                creative_script_path,
                render_creative_script_md(
                    matched=matched,
                    system_title=system_title,
                    source_md_rel=source_md_rel,
                    script_text=script_text,
                    share_url=source_url,
                    file_id=file_id,
                    fetched_at=fetched_at,
                    digest=digest,
                ),
            )
            creative_script_rel = rel(creative_script_path, wiki_path)

        records.append(
            {
                "index": index,
                "file_id": file_id,
                "title": detail.get("tl") or item.get("tl") or "",
                "system_title": system_title,
                "char_count": len("\n".join(lines)),
                "script_char_count": len(script_text),
                "source_url": source_url,
                "source_md": source_md_rel,
                "raw_json": rel(raw_json_path, wiki_path),
                "matched_xhs_note_id": matched.note_id if matched else "",
                "matched_xhs_title": matched.title if matched else "",
                "match_score": round(match_score, 4),
                "creative_script": creative_script_rel,
                "sha256": digest,
            }
        )

    manifest = {
        "success": True,
        "share_id": share_id,
        "share_url": share_url,
        "fetched_at": fetched_at,
        "notebook_title": notebook[1],
        "notebook_count": notebook[0],
        "exported_count": len(records),
        "matched_count": sum(1 for record in records if record["matched_xhs_note_id"]),
        "unmatched_count": sum(1 for record in records if not record["matched_xhs_note_id"]),
        "output_root": rel(output_root, wiki_path),
        "records": records,
    }
    write_json(output_root / "manifest.json", manifest)
    write_json(wiki_path / "_derived" / "youdao-xhs-scripts-manifest.json", manifest)

    index_lines = [
        "---",
        'title: "Youdao XHS Scripts"',
        'type: "raw/xhs-youdao-script-index"',
        f'source_url: "{share_url}"',
        f'share_id: "{share_id}"',
        f'fetched_at: "{fetched_at}"',
        f"exported_count: {manifest['exported_count']}",
        f"matched_count: {manifest['matched_count']}",
        "---",
        "",
        "# Youdao XHS Scripts",
        "",
        f"- notebook: {notebook[1]}",
        f"- exported: {manifest['exported_count']}",
        f"- matched to existing XHS notes: {manifest['matched_count']}",
        f"- unmatched: {manifest['unmatched_count']}",
        "",
        "| # | Youdao Title | System Title | Matched XHS | Source |",
        "| ---: | --- | --- | --- | --- |",
    ]
    for record in records:
        matched_label = (
            f"[[raw/xhs/notes/{record['matched_xhs_note_id']}/note|{record['matched_xhs_title']}]]"
            if record["matched_xhs_note_id"]
            else ""
        )
        index_lines.append(
            "| "
            + " | ".join(
                [
                    str(record["index"]),
                    record["title"].replace("|", "\\|"),
                    record["system_title"].replace("|", "\\|"),
                    matched_label,
                    f"[[{record['source_md'].removesuffix('.md')}|source]]",
                ]
            )
            + " |"
        )
    write_text(output_root / "index.md", "\n".join(index_lines) + "\n")
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--share-url", default=DEFAULT_SHARE_URL)
    parser.add_argument("--wiki-path", type=Path, default=DEFAULT_WIKI_PATH)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = ingest_youdao_xhs_scripts(
        share_url=args.share_url,
        wiki_path=args.wiki_path,
    )
    if args.json:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    else:
        print(f"Exported: {manifest['exported_count']}")
        print(f"Matched: {manifest['matched_count']}")
        print(f"Output: {manifest['output_root']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
