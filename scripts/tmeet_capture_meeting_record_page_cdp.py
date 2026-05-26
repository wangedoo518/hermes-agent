#!/usr/bin/env python3
"""Capture Tencent Meeting web record-page data through Chrome CDP.

This is a companion adapter for the official `tmeet` CLI exporter. The CLI
remains the primary source of structured records; this script is for the cases
where the CLI is rate-limited or missing browser-only assets. It connects to a
locally running Chrome with remote debugging enabled, opens the Tencent Meeting
record center, captures relevant JSON responses, and writes a compact evidence
bundle into the Obsidian llm-wiki raw area.

The script intentionally does not store cookies, request headers, or auth
tokens. It only stores response metadata/body and a small DOM text snapshot.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


DEFAULT_CDP_URL = "http://127.0.0.1:9222"
DEFAULT_RECORD_URL = "https://meeting.tencent.com/user-center/meeting-record"
DEFAULT_WIKI_PATH = Path(
    os.getenv("HERMES_XHS_WIKI_PATH")
    or os.getenv("HERMES_CREATOR_WIKI_PATH")
    or os.getenv("LUFEI_XHS_WIKI_PATH")
    or os.getenv("XHS_WIKI_PATH")
    or os.getenv("WIKI_PATH")
    or "/Users/champion/Documents/develop/lufei/wiki"
).expanduser()
MATCH_PATTERNS = (
    "meetlog",
    "cloud-record",
    "record",
    "transcript",
    "minutes",
    "meeting-record",
)
RECORD_LIST_ENDPOINT = "my-record-list"


class TmeetCdpCaptureError(RuntimeError):
    """Raised when the CDP capture cannot be initialized."""


def now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def safe_slug(text: str, *, limit: int = 96) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-")
    return (cleaned[:limit] or "capture").strip("-")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def body_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def should_capture(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if not (
        host.endswith("meeting.tencent.com")
        or host.endswith("tencentmeeting.com")
        or "wemeet" in host
    ):
        return False
    normalized_path = parsed.path.lower()
    return any(pattern in normalized_path for pattern in MATCH_PATTERNS)


def is_login_page(url: str, body_text: str) -> bool:
    markers = (
        "login.html",
        "腾讯会议登录",
        "扫码登录",
        "企业微信登录",
        "微信登录",
        "手机号登录",
    )
    payload = f"{url}\n{body_text}"
    return any(marker in payload for marker in markers)


def maybe_json(text: str, content_type: str) -> Any:
    stripped = text.strip()
    if "json" not in content_type.lower() and not stripped.startswith(("{", "[")):
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def response_filename(index: int, url: str, content_type: str) -> str:
    ext = "json" if "json" in content_type.lower() else "txt"
    slug = safe_slug(url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1] or "response")
    return f"{index:03d}-{slug}.{ext}"


def source_markdown(report: dict[str, Any]) -> str:
    yaml_lines = [
        "---",
        f"title: {json.dumps(report['title'], ensure_ascii=False)}",
        'type: "raw/tencent-meeting-web-cdp"',
        f"source_url: {json.dumps(report['record_url'], ensure_ascii=False)}",
        f"captured_at: {json.dumps(report['captured_at'], ensure_ascii=False)}",
        f"requires_web_login: {'true' if report['requires_web_login'] else 'false'}",
        f"response_count: {report['response_count']}",
        f"json_response_count: {report['json_response_count']}",
        f"sha256: {json.dumps(report['sha256'], ensure_ascii=False)}",
        "---",
        "",
        f"# {report['title']}",
        "",
        "## Capture Summary",
        "",
        f"- Final URL: {report['final_url']}",
        f"- Requires web login: {report['requires_web_login']}",
        f"- Captured responses: {report['response_count']}",
        f"- Captured JSON responses: {report['json_response_count']}",
        "",
        "## Files",
        "",
        "- Report: [[capture_report.json]]",
        "- DOM snapshot: [[dom.md]]",
        "- Record list: [[record_list.md]]",
        "- Responses: `responses/`",
        "",
        "## Notes",
        "",
        "- Captured with `scripts/tmeet_capture_meeting_record_page_cdp.py`.",
        "- This adapter stores response bodies and DOM text only; it does not store cookies or request headers.",
        "- If `requires_web_login` is true, open the Tencent Meeting record page in the same Chrome profile, finish login, and run the capture again.",
    ]
    return "\n".join(yaml_lines) + "\n"


def record_time(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        timestamp = int(str(value))
    except ValueError:
        return str(value)
    if timestamp > 10_000_000_000:
        timestamp //= 1000
    return datetime.fromtimestamp(timestamp, ZoneInfo("Asia/Shanghai")).strftime(
        "%Y-%m-%d %H:%M"
    )


def classify_title(title: str) -> str:
    coaching_keywords = (
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
    course_keywords = (
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
    normalized = title.lower()
    is_coaching = any(keyword.lower() in normalized for keyword in coaching_keywords)
    is_course = any(keyword.lower() in normalized for keyword in course_keywords)
    if is_coaching and not is_course:
        return "coaching"
    if is_course and not is_coaching:
        return "courses"
    return "unknown"


def normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    meeting_info = record.get("meeting_info") if isinstance(record.get("meeting_info"), dict) else {}
    title = str(record.get("title") or meeting_info.get("subject") or "untitled")
    return {
        "record_id": record.get("record_id") or record.get("encode_record_id"),
        "uni_record_id": record.get("uni_record_id"),
        "file_id": record.get("file_id"),
        "title": title,
        "category": classify_title(title),
        "meeting_code": meeting_info.get("meeting_code"),
        "meeting_id": meeting_info.get("meeting_id"),
        "start_time": record.get("start_time"),
        "start_time_text": record_time(record.get("start_time")),
        "end_time": record.get("end_time"),
        "end_time_text": record_time(record.get("end_time")),
        "duration_ms": record.get("duration"),
        "size": record.get("size"),
        "recorder_username": record.get("recorder_username"),
        "record_type": record.get("record_type"),
        "record_count": record.get("record_count"),
        "allow_download": record.get("allow_download"),
        "jump_path": record.get("jump_path"),
        "share_url": record.get("share_url"),
    }


def extract_record_payload(text: str, request_post_data: str | None) -> dict[str, Any] | None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None
    records = data.get("records")
    if not isinstance(records, list):
        return None
    page_index = None
    page_size = None
    if request_post_data:
        try:
            request_payload = json.loads(request_post_data)
            page_index = request_payload.get("page_index")
            page_size = request_payload.get("page_size")
        except json.JSONDecodeError:
            pass
    page_info = data.get("page_info") if isinstance(data.get("page_info"), dict) else {}
    return {
        "page_index": int(page_index or page_info.get("index") or 0),
        "page_size": int(page_size or page_info.get("size") or len(records) or 0),
        "total_count": int(page_info.get("count") or 0),
        "has_more": page_info.get("has_more"),
        "last_index": data.get("last_index"),
        "records": [record for record in records if isinstance(record, dict)],
    }


def record_list_markdown(report: dict[str, Any], records: list[dict[str, Any]]) -> str:
    yaml_lines = [
        "---",
        f"title: {json.dumps('Tencent Meeting CDP Record List', ensure_ascii=False)}",
        'type: "raw/tencent-meeting-record-list"',
        f"source_url: {json.dumps(report['record_url'], ensure_ascii=False)}",
        f"captured_at: {json.dumps(report['captured_at'], ensure_ascii=False)}",
        f"record_count: {len(records)}",
        f"sha256: {json.dumps(sha256_json(records), ensure_ascii=False)}",
        "---",
        "",
        "# Tencent Meeting CDP Record List",
        "",
        f"- Captured: {report['captured_at']}",
        f"- Records: {len(records)}",
        f"- Source: {report['record_url']}",
        "",
        "| # | Time | Category | Title | Meeting Code | Duration | Record ID |",
        "|---:|---|---|---|---|---|---|",
    ]
    for index, record in enumerate(records, start=1):
        title = str(record.get("title") or "").replace("|", "\\|")
        yaml_lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    str(record.get("start_time_text") or ""),
                    str(record.get("category") or ""),
                    title,
                    str(record.get("meeting_code") or ""),
                    str(record.get("duration_ms") or ""),
                    str(record.get("record_id") or ""),
                ]
            )
            + " |"
        )
    return "\n".join(yaml_lines) + "\n"


def write_record_list_outputs(capture_root: Path, report: dict[str, Any], pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    records: list[dict[str, Any]] = []
    for page_payload in sorted(pages, key=lambda item: item.get("page_index") or 0):
        for raw_record in page_payload.get("records", []):
            record = normalize_record(raw_record)
            record_key = str(
                record.get("record_id")
                or record.get("uni_record_id")
                or record.get("file_id")
                or sha256_json(record)
            )
            if record_key in seen:
                continue
            seen.add(record_key)
            records.append(record)
    write_json(capture_root / "record_list.json", {"pages": pages, "records": records})
    write_text(capture_root / "record_list.md", record_list_markdown(report, records))
    return records


def capture_meeting_record_page(
    *,
    cdp_url: str,
    record_url: str,
    wiki_path: Path,
    wait_ms: int,
    max_pages: int = 1,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        raise TmeetCdpCaptureError(
            "Playwright is required for Chrome CDP capture. Install with `pip install playwright`."
        ) from exc

    captured_at = now_iso()
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S")
    capture_root = output_dir or wiki_path / "raw" / "tencent-meetings" / "cdp-captures" / stamp
    response_dir = capture_root / "responses"
    response_dir.mkdir(parents=True, exist_ok=True)

    responses: list[dict[str, Any]] = []
    record_list_pages: list[dict[str, Any]] = []

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(cdp_url)
        except PlaywrightError as exc:
            raise TmeetCdpCaptureError(
                f"Cannot connect to Chrome CDP at {cdp_url}. Start Chrome with remote debugging first."
            ) from exc

        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()

        def on_response(response) -> None:  # type: ignore[no-untyped-def]
            url = response.url
            if not should_capture(url):
                return
            try:
                status = response.status
                headers = response.headers
                content_type = headers.get("content-type", "")
                text = response.text()
                request_post_data = response.request.post_data
            except Exception as exc:  # noqa: BLE001
                responses.append(
                    {
                        "url": url,
                        "status": getattr(response, "status", None),
                        "error": str(exc),
                    }
                )
                return

            parsed_json = maybe_json(text, content_type)
            filename = response_filename(len(responses) + 1, url, content_type)
            body_path = response_dir / filename
            if parsed_json is not None:
                write_json(body_path, parsed_json)
            else:
                write_text(body_path, text[:1_000_000])

            responses.append(
                {
                    "url": url,
                    "status": status,
                    "method": response.request.method,
                    "request_post_data": request_post_data,
                    "content_type": content_type,
                    "body_file": str(body_path.relative_to(capture_root)),
                    "body_sha256": body_hash(text),
                    "body_size": len(text.encode("utf-8", errors="ignore")),
                    "is_json": parsed_json is not None,
                }
            )
            if RECORD_LIST_ENDPOINT in url:
                record_payload = extract_record_payload(text, request_post_data)
                if record_payload:
                    record_list_pages.append(record_payload)

        page.on("response", on_response)
        try:
            page.goto(record_url, wait_until="domcontentloaded", timeout=60_000)
        except PlaywrightTimeoutError:
            # Keep the partial capture; many Tencent Meeting assets arrive before
            # network idle, especially on login/redirect pages.
            pass
        page.wait_for_timeout(wait_ms)

        total_pages = 1
        if record_list_pages:
            first_page = record_list_pages[0]
            total_count = first_page.get("total_count") or 0
            page_size = first_page.get("page_size") or 10
            if total_count and page_size:
                total_pages = max(1, (int(total_count) + int(page_size) - 1) // int(page_size))
        pages_to_visit = total_pages if max_pages <= 0 else min(max_pages, total_pages)
        for _ in range(2, pages_to_visit + 1):
            try:
                next_button = page.locator(
                    "button.met-pagination__pagechoosebtn:not(.is-disabled)"
                ).last
                before = len(record_list_pages)
                next_button.click(timeout=5_000)
                page.wait_for_timeout(1200)
                if len(record_list_pages) == before:
                    page.wait_for_timeout(1800)
            except Exception:  # noqa: BLE001
                break

        title = page.title()
        final_url = page.url
        try:
            body_text = page.locator("body").inner_text(timeout=5_000)
        except Exception:  # noqa: BLE001
            body_text = ""
        requires_web_login = is_login_page(final_url, body_text)

        write_text(
            capture_root / "dom.md",
            "\n".join(
                [
                    f"# DOM Snapshot - {title or 'Tencent Meeting'}",
                    "",
                    f"- URL: {final_url}",
                    f"- Captured: {captured_at}",
                    "",
                    "```text",
                    body_text[:30_000],
                    "```",
                ]
            ),
        )
        page.close()

    response_count = len(responses)
    json_response_count = sum(1 for item in responses if item.get("is_json"))
    report_seed = {
        "record_url": record_url,
        "final_url": final_url,
        "responses": responses,
        "dom_sha256": body_hash(body_text),
    }
    report = {
        "success": True,
        "title": title or "Tencent Meeting Record Center CDP Capture",
        "captured_at": captured_at,
        "record_url": record_url,
        "final_url": final_url,
        "capture_root": str(capture_root),
        "requires_web_login": requires_web_login,
        "response_count": response_count,
        "json_response_count": json_response_count,
        "record_list_page_count": len(record_list_pages),
        "record_list_total_count": (
            record_list_pages[0].get("total_count") if record_list_pages else 0
        ),
        "responses": responses,
        "sha256": sha256_json(report_seed),
        "warnings": [],
    }
    if requires_web_login:
        report["warnings"].append(
            "Chrome is not logged into meeting.tencent.com; login in the same Chrome profile and rerun."
        )
    if response_count == 0:
        report["warnings"].append(
            "No Tencent Meeting record API responses were captured. Keep the page open longer or interact with the record list."
        )
    elif json_response_count == 0:
        report["warnings"].append(
            "No Tencent Meeting record JSON responses were captured. If the page is still loading, increase wait_ms; if it redirects to login, complete web login and rerun."
        )
    records = write_record_list_outputs(capture_root, report, record_list_pages)
    report["record_list_exported_count"] = len(records)

    write_json(capture_root / "capture_report.json", report)
    write_text(capture_root / "source.md", source_markdown(report))
    write_json(wiki_path / "_derived" / "tencent-meetings-cdp-latest.json", report)
    write_json(
        wiki_path / "_derived" / "tencent-meetings-cdp-record-list-latest.json",
        {"capture_root": str(capture_root), "records": records},
    )
    return report


def sha256_json(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture Tencent Meeting record-center responses through Chrome CDP."
    )
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    parser.add_argument("--url", default=DEFAULT_RECORD_URL)
    parser.add_argument("--wiki-path", type=Path, default=DEFAULT_WIKI_PATH)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--wait-ms", type=int, default=15_000)
    parser.add_argument(
        "--max-pages",
        type=int,
        default=1,
        help="Maximum record-list pages to visit after the first page. Use 0 for all pages.",
    )
    parser.add_argument("--json", action="store_true", help="Print the report as JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        report = capture_meeting_record_page(
            cdp_url=args.cdp_url,
            record_url=args.url,
            wiki_path=args.wiki_path,
            wait_ms=args.wait_ms,
            max_pages=args.max_pages,
            output_dir=args.output_dir,
        )
    except Exception as exc:  # noqa: BLE001
        if args.json:
            print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Capture written to {report['capture_root']}")
        if report.get("warnings"):
            print("Warnings:")
            for warning in report["warnings"]:
                print(f"- {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
