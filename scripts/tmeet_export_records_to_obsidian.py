#!/usr/bin/env python3
"""Export Tencent Meeting records into an Obsidian llm-wiki raw folder.

The script intentionally keeps this as a file adapter. It does not depend on an
Obsidian API, and it does not mutate concept/persona pages. Raw meeting assets
are written first; downstream skills can consume the generated manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


DEFAULT_WIKI_PATH = Path(
    "/Users/champion/Documents/develop/Wiki/ClaudeWiki/lufei-xhs-wiki"
)
DEFAULT_TMEET_REPO = Path("/Users/champion/Documents/develop/tencentmeeting-cli")
PAGE_SIZE = 30

COACHING_KEYWORDS = (
    "辅导",
    "咨询",
    "1v1",
    "一对一",
    "作品集点评",
    "作品集",
    "简历",
    "面试辅导",
    "模拟面试",
    "复盘",
    "学员",
)
COURSE_KEYWORDS = (
    "课程",
    "训练营",
    "web coding",
    "前端",
    "直播课",
    "录播课",
    "公开课",
    "答疑课",
    "作业点评",
    "讲义",
)


class TmeetExportError(RuntimeError):
    """Raised when the export cannot continue."""


@dataclass(frozen=True)
class CommandResult:
    data: Any
    raw: dict[str, Any]


class BaseRunner:
    def run(self, args: list[str]) -> CommandResult:
        raise NotImplementedError


class TmeetRunner(BaseRunner):
    def __init__(
        self,
        *,
        tmeet_bin: str | None = None,
        tmeet_repo: Path = DEFAULT_TMEET_REPO,
        timeout: int = 120,
    ) -> None:
        self.timeout = timeout
        found = tmeet_bin or shutil.which("tmeet")
        local_binary = tmeet_repo / "tmeet"
        if found:
            self.mode = "bin"
            self.command = [found]
            self.cwd: Path | None = None
        elif local_binary.exists():
            self.mode = "local-bin"
            self.command = [str(local_binary)]
            self.cwd = None
        elif tmeet_repo.exists() and shutil.which("go"):
            self.mode = "go-run"
            self.command = ["go", "run", "."]
            self.cwd = tmeet_repo
        else:
            raise TmeetExportError(
                "Cannot find tmeet. Install with `npm install -g @tencentcloud/tmeet` "
                "or keep the source repo at "
                f"{tmeet_repo} with Go installed."
            )

    def run(self, args: list[str]) -> CommandResult:
        cmd = [*self.command, *args]
        proc = subprocess.run(
            cmd,
            cwd=self.cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=self.timeout,
            check=False,
        )
        output = (proc.stdout or "").strip()
        if proc.returncode != 0:
            raise TmeetExportError(
                f"tmeet command failed ({proc.returncode}): {' '.join(args)}\n"
                f"{proc.stderr.strip() or output}"
            )
        parsed = parse_json_output(output)
        return CommandResult(data=parsed.get("data"), raw=parsed)


class FixtureRunner(BaseRunner):
    """Read canned command responses from a directory for deterministic tests."""

    def __init__(self, fixture_dir: Path) -> None:
        self.fixture_dir = fixture_dir

    def run(self, args: list[str]) -> CommandResult:
        name = fixture_name_for_args(args)
        path = self.fixture_dir / name
        if not path.exists():
            # Missing optional child assets should behave like a soft tmeet miss.
            return CommandResult(data={}, raw={"message": f"missing fixture: {name}", "data": {}})
        parsed = json.loads(path.read_text(encoding="utf-8"))
        return CommandResult(data=parsed.get("data"), raw=parsed)


def parse_json_output(output: str) -> dict[str, Any]:
    if not output:
        raise TmeetExportError("tmeet returned empty output")
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        start = output.find("{")
        end = output.rfind("}")
        if start < 0 or end < start:
            raise TmeetExportError(f"tmeet output is not JSON: {output[:300]}") from None
        parsed = json.loads(output[start : end + 1])
    if not isinstance(parsed, dict):
        raise TmeetExportError("tmeet output root is not a JSON object")
    return parsed


def fixture_name_for_args(args: list[str]) -> str:
    if args[:2] == ["meeting", "list-ended"]:
        token = value_after(args, "--page-token")
        return f"meeting_list_ended_{slug_part(token)}.json" if token else "meeting_list_ended.json"
    if args[:2] == ["record", "list"]:
        token = value_after(args, "--page-token")
        return f"record_list_{slug_part(token)}.json" if token else "record_list.json"
    if args[:2] == ["record", "address"]:
        return f"record_address_{slug_part(value_after(args, '--meeting-record-id'))}.json"
    if args[:2] == ["record", "transcript-paragraphs"]:
        return f"transcript_paragraphs_{slug_part(value_after(args, '--record-file-id'))}.json"
    if args[:2] == ["record", "smart-minutes"]:
        return f"smart_minutes_{slug_part(value_after(args, '--record-file-id'))}.json"
    if args[:2] == ["auth", "status"]:
        return "auth_status.json"
    return f"{slug_part('_'.join(args))}.json"


def value_after(args: list[str], flag: str) -> str:
    try:
        idx = args.index(flag)
    except ValueError:
        return ""
    if idx + 1 >= len(args):
        return ""
    return args[idx + 1]


def slug_part(value: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value or "none").strip("-") or "none"


def iso_boundary(date_value: str, *, end: bool = False) -> str:
    if "T" in date_value:
        return date_value
    suffix = "23:59:59+08:00" if end else "00:00:00+08:00"
    return f"{date_value}T{suffix}"


def now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def classify_meeting(title: str) -> tuple[str, float, bool]:
    normalized = title.lower()
    coaching = any(keyword.lower() in normalized for keyword in COACHING_KEYWORDS)
    course = any(keyword.lower() in normalized for keyword in COURSE_KEYWORDS)
    if coaching and not course:
        return "coaching", 0.9, True
    if course and not coaching:
        return "courses", 0.9, False
    if coaching and course:
        return "unknown", 0.45, True
    return "unknown", 0.2, False


def collect_paginated(
    runner: BaseRunner,
    base_args: list[str],
    *,
    item_keys: tuple[str, ...],
    warnings: list[str],
) -> list[dict[str, Any]]:
    token = ""
    all_items: list[dict[str, Any]] = []
    seen_tokens: set[str] = set()
    while True:
        args = [*base_args, "--page-size", str(PAGE_SIZE), "--compact"]
        if token:
            args.extend(["--page-token", token])
        result = runner.run(args)
        data = result.data
        all_items.extend(extract_items(data, item_keys))
        next_token = extract_next_page_token(data)
        if not next_token:
            break
        if next_token in seen_tokens:
            warnings.append(f"Pagination token repeated for {' '.join(base_args)}: {next_token}")
            break
        seen_tokens.add(next_token)
        token = next_token
    return all_items


def extract_items(data: Any, item_keys: tuple[str, ...]) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    for key in item_keys:
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    for value in data.values():
        if isinstance(value, list) and all(isinstance(item, dict) for item in value):
            return list(value)
    if any(k in data for k in ("meeting_id", "meeting_code", "meeting_record_id", "record_file_id")):
        return [data]
    return []


def extract_next_page_token(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    value = data.get("next_page_token") or data.get("nextPageToken")
    return str(value) if value else ""


def id_value(obj: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = obj.get(key)
        if value is not None and value != "":
            return str(value)
    return ""


def title_from(meeting: dict[str, Any] | None, record: dict[str, Any]) -> str:
    candidates: list[Any] = []
    if meeting:
        candidates.extend(
            meeting.get(key)
            for key in ("subject", "meeting_subject", "title", "name", "meeting_name")
        )
    candidates.extend(
        record.get(key)
        for key in ("subject", "meeting_subject", "title", "name", "meeting_name")
    )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return "untitled-tencent-meeting"


def meeting_lookup(meetings: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for meeting in meetings:
        for key in ("meeting_id", "meeting_code", "sub_meeting_id"):
            value = meeting.get(key)
            if value:
                lookup[str(value)] = meeting
    return lookup


def match_meeting(record: dict[str, Any], lookup: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    for key in ("meeting_id", "meeting_code", "sub_meeting_id"):
        value = record.get(key)
        if value and str(value) in lookup:
            return lookup[str(value)]
    return None


def record_files(record: dict[str, Any]) -> list[dict[str, Any]]:
    direct = id_value(record, ("record_file_id", "file_id"))
    if direct:
        return [{"record_file_id": direct, **record}]
    for key in ("record_files", "record_file_list", "files", "file_list"):
        value = record.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def first_file_id(record: dict[str, Any]) -> str:
    files = record_files(record)
    if not files:
        return ""
    return id_value(files[0], ("record_file_id", "file_id", "id"))


def sha256_json(*payloads: Any) -> str:
    text = json.dumps(payloads, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def slugify(text: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|#\n\r\t]+", "-", text).strip(" .-")
    cleaned = re.sub(r"\s+", "-", cleaned)
    return cleaned[:80] or "untitled"


def date_prefix(meeting: dict[str, Any] | None, record: dict[str, Any]) -> str:
    for source in (record, meeting or {}):
        for key in (
            "record_start_time",
            "record_end_time",
            "media_start_time",
            "start_time",
            "end_time",
            "meeting_start_time",
        ):
            value = source.get(key)
            if not value:
                continue
            parsed = parse_date_prefix(str(value))
            if parsed:
                return parsed
    return datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")


def parse_date_prefix(value: str) -> str:
    value = value.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", value):
        return value[:10]
    if value.isdigit():
        timestamp = int(value)
        if timestamp > 10_000_000_000:
            timestamp //= 1000
        return datetime.fromtimestamp(timestamp, ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    return ""


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def transcript_to_markdown(data: Any) -> str:
    paragraphs = extract_items(
        data,
        (
            "paragraphs",
            "transcripts",
            "transcript_list",
            "paragraph_list",
            "list",
            "items",
        ),
    )
    if not paragraphs:
        return "## Transcript\n\nNo transcript paragraphs returned.\n"
    lines = ["## Transcript", ""]
    for item in paragraphs:
        speaker = id_value(item, ("speaker", "speaker_name", "user_name", "nickname"))
        start = id_value(item, ("start_time", "start", "begin_time"))
        text = id_value(item, ("text", "content", "paragraph", "sentence"))
        prefix_parts = [part for part in (start, speaker) if part]
        prefix = f"**{' / '.join(prefix_parts)}**: " if prefix_parts else ""
        if text:
            lines.append(f"- {prefix}{text}")
    return "\n".join(lines) + "\n"


def smart_minutes_to_markdown(data: Any) -> str:
    if isinstance(data, str):
        return f"## Smart Minutes\n\n{data}\n"
    if not isinstance(data, dict):
        return "## Smart Minutes\n\nNo smart minutes returned.\n"
    lines = ["## Smart Minutes", ""]
    for key in ("summary", "abstract", "content", "minutes", "outline"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            lines.extend([f"### {key}", "", value.strip(), ""])
        elif isinstance(value, list):
            lines.extend([f"### {key}", ""])
            for item in value:
                lines.append(f"- {item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)}")
            lines.append("")
    if len(lines) <= 2:
        lines.append("No smart minutes content returned.")
    return "\n".join(lines) + "\n"


def source_markdown(frontmatter: dict[str, Any], files: dict[str, str]) -> str:
    yaml_lines = ["---"]
    for key, value in frontmatter.items():
        if isinstance(value, list):
            rendered = "[" + ", ".join(json.dumps(v, ensure_ascii=False) for v in value) + "]"
        elif isinstance(value, bool):
            rendered = "true" if value else "false"
        else:
            rendered = json.dumps(value, ensure_ascii=False)
        yaml_lines.append(f"{key}: {rendered}")
    yaml_lines.append("---")
    body = [
        "",
        f"# {frontmatter['title']}",
        "",
        "## Assets",
        "",
    ]
    for label, filename in files.items():
        body.append(f"- {label}: [[{filename}]]")
    body.extend(
        [
            "",
            "## Notes",
            "",
            "- Exported with `scripts/tmeet_export_records_to_obsidian.py`.",
            "- This page is raw material for llm-wiki; concept pages should cite it before deriving conclusions.",
        ]
    )
    return "\n".join(yaml_lines + body) + "\n"


def export_records(
    *,
    runner: BaseRunner,
    wiki_path: Path,
    start: str,
    end: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    warnings: list[str] = []
    start_iso = iso_boundary(start)
    end_iso = iso_boundary(end, end=True)
    raw_root = wiki_path / "raw" / "tencent-meetings"
    derived_root = wiki_path / "_derived"

    meetings = collect_paginated(
        runner,
        ["meeting", "list-ended", "--start", start_iso, "--end", end_iso],
        item_keys=("meetings", "meeting_info_list", "meeting_list", "items", "list"),
        warnings=warnings,
    )
    records = collect_paginated(
        runner,
        ["record", "list", "--start", start_iso, "--end", end_iso],
        item_keys=("records", "record_list", "record_meetings", "items", "list"),
        warnings=warnings,
    )

    lookup = meeting_lookup(meetings)
    manifest_records: list[dict[str, Any]] = []
    category_counts = {"coaching": 0, "courses": 0, "unknown": 0}

    for index, record in enumerate(records, start=1):
        meeting = match_meeting(record, lookup)
        title = title_from(meeting, record)
        category, confidence, contains_student_info = classify_meeting(title)
        category_counts[category] += 1

        meeting_id = id_value(record, ("meeting_id", "sub_meeting_id")) or id_value(
            meeting or {}, ("meeting_id", "sub_meeting_id")
        )
        meeting_code = id_value(record, ("meeting_code",)) or id_value(meeting or {}, ("meeting_code",))
        meeting_record_id = id_value(record, ("meeting_record_id", "record_id", "id"))
        record_file_id = first_file_id(record)

        prefix = date_prefix(meeting, record)
        stable_id = meeting_record_id or record_file_id or meeting_id or str(index)
        directory = raw_root / category / f"{prefix}-{slugify(title)}-{slug_part(stable_id)[:16]}"

        address_raw: dict[str, Any] = {}
        transcript_raw: dict[str, Any] = {}
        minutes_raw: dict[str, Any] = {}

        if meeting_record_id:
            try:
                address_raw = runner.run(
                    ["record", "address", "--meeting-record-id", meeting_record_id, "--compact"]
                ).raw
            except Exception as exc:  # noqa: BLE001 - keep exporting other assets
                warnings.append(f"{title}: record address failed: {exc}")
        if record_file_id:
            try:
                transcript_raw = runner.run(
                    [
                        "record",
                        "transcript-paragraphs",
                        "--record-file-id",
                        record_file_id,
                        *(
                            ["--meeting-id", meeting_id]
                            if meeting_id
                            else []
                        ),
                        "--format",
                        "json",
                    ]
                ).raw
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{title}: transcript paragraphs failed: {exc}")
            try:
                minutes_raw = runner.run(
                    [
                        "record",
                        "smart-minutes",
                        "--record-file-id",
                        record_file_id,
                        "--lang",
                        "zh",
                        "--format",
                        "json",
                    ]
                ).raw
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{title}: smart minutes failed: {exc}")

        content_hash = sha256_json(meeting, record, address_raw, transcript_raw, minutes_raw)
        frontmatter = {
            "title": title,
            "type": "raw/tencent-meeting",
            "category": category,
            "category_confidence": confidence,
            "visibility": "local",
            "contains_student_info": contains_student_info,
            "needs_anonymization_before_external_use": contains_student_info,
            "meeting_id": meeting_id,
            "meeting_code": meeting_code,
            "meeting_record_id": meeting_record_id,
            "record_file_id": record_file_id,
            "source": "tmeet",
            "source_url": "https://meeting.tencent.com/user-center/meeting-record",
            "ingested": now_iso(),
            "sha256": content_hash,
            "has_transcript": bool(transcript_raw.get("data")),
            "has_smart_minutes": bool(minutes_raw.get("data")),
            "tags": ["tencent-meeting", "lufei", category],
        }

        rel_dir = directory.relative_to(wiki_path)
        manifest_entry = {
            "title": title,
            "category": category,
            "category_confidence": confidence,
            "contains_student_info": contains_student_info,
            "meeting_id": meeting_id,
            "meeting_code": meeting_code,
            "meeting_record_id": meeting_record_id,
            "record_file_id": record_file_id,
            "sha256": content_hash,
            "path": str(rel_dir),
            "source_md": str(rel_dir / "source.md"),
        }
        manifest_records.append(manifest_entry)

        if dry_run:
            continue

        write_json(directory / "meeting.json", meeting or {})
        write_json(directory / "record.json", record)
        write_json(directory / "record_address.json", address_raw)
        write_json(directory / "transcript.paragraphs.json", transcript_raw)
        write_text(directory / "transcript.md", transcript_to_markdown(transcript_raw.get("data")))
        write_json(directory / "smart-minutes.json", minutes_raw)
        write_text(directory / "smart-minutes.md", smart_minutes_to_markdown(minutes_raw.get("data")))
        write_text(
            directory / "source.md",
            source_markdown(
                frontmatter,
                {
                    "meeting": "meeting.json",
                    "record": "record.json",
                    "record address": "record_address.json",
                    "transcript": "transcript.md",
                    "smart minutes": "smart-minutes.md",
                },
            ),
        )

    manifest = {
        "type": "derived/tencent-meetings-manifest",
        "source": "tmeet",
        "source_url": "https://meeting.tencent.com/user-center/meeting-record",
        "start": start_iso,
        "end": end_iso,
        "ingested": now_iso(),
        "meeting_count": len(meetings),
        "record_count": len(records),
        "exported_count": len(manifest_records),
        "category_counts": category_counts,
        "records": manifest_records,
        "warnings": warnings,
    }

    if not dry_run:
        write_json(raw_root / "manifest.json", manifest)
        write_json(derived_root / "tencent-meetings-manifest.json", manifest)
        write_text(raw_root / "index.md", index_markdown(manifest))

    return manifest


def index_markdown(manifest: dict[str, Any]) -> str:
    lines = [
        "---",
        'title: "Tencent Meeting Raw Index"',
        'type: "raw/tencent-meetings-index"',
        f"source_url: {json.dumps(manifest['source_url'], ensure_ascii=False)}",
        f"ingested: {json.dumps(manifest['ingested'], ensure_ascii=False)}",
        f"sha256: {json.dumps(sha256_json(manifest), ensure_ascii=False)}",
        "---",
        "",
        "# Tencent Meeting Raw Index",
        "",
        f"- Time range: `{manifest['start']}` to `{manifest['end']}`",
        f"- Exported records: {manifest['exported_count']}",
        f"- Coaching: {manifest['category_counts']['coaching']}",
        f"- Courses: {manifest['category_counts']['courses']}",
        f"- Unknown: {manifest['category_counts']['unknown']}",
        "",
        "## Records",
        "",
    ]
    for item in manifest["records"]:
        lines.append(
            f"- [{item['category']}] [[{item['source_md'][:-3]}]] "
            f"- {item['title']}"
        )
    if manifest.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in manifest["warnings"])
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="Start date or ISO time, e.g. 2026-05-01")
    parser.add_argument("--end", required=True, help="End date or ISO time, e.g. 2026-05-23")
    parser.add_argument("--wiki-path", type=Path, default=DEFAULT_WIKI_PATH)
    parser.add_argument("--tmeet-bin", help="Optional explicit tmeet binary path")
    parser.add_argument("--tmeet-repo", type=Path, default=DEFAULT_TMEET_REPO)
    parser.add_argument("--fixture-dir", type=Path, help="Use JSON fixtures instead of calling tmeet")
    parser.add_argument("--dry-run", action="store_true", help="Collect and classify without writing files")
    parser.add_argument("--json", action="store_true", help="Print the export manifest as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    runner: BaseRunner
    if args.fixture_dir:
        runner = FixtureRunner(args.fixture_dir)
    else:
        runner = TmeetRunner(tmeet_bin=args.tmeet_bin, tmeet_repo=args.tmeet_repo)
    try:
        manifest = export_records(
            runner=runner,
            wiki_path=args.wiki_path,
            start=args.start,
            end=args.end,
            dry_run=args.dry_run,
        )
    except TmeetExportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    else:
        print(
            "Exported "
            f"{manifest['exported_count']} Tencent Meeting record(s) to "
            f"{args.wiki_path / 'raw' / 'tencent-meetings'}"
        )
        if manifest.get("warnings"):
            print(f"Warnings: {len(manifest['warnings'])}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
