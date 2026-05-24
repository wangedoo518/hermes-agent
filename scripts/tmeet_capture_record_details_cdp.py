#!/usr/bin/env python3
"""Capture Tencent Meeting record detail pages through Chrome CDP.

This script consumes the full record list produced by
`tmeet_capture_meeting_record_page_cdp.py`, opens each record's web detail page
in an already logged-in Chrome profile, and writes DOM-rendered meeting minutes,
transcript text, and response evidence into the Obsidian llm-wiki raw area.

It deliberately stores response bodies and DOM text only. It does not store
cookies, request headers, browser storage, or auth tokens.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


DEFAULT_CDP_URL = "http://127.0.0.1:9222"
DEFAULT_WIKI_PATH = Path(
    "/Users/champion/Documents/develop/Wiki/ClaudeWiki/lufei-xhs-wiki"
)
DETAIL_MATCH_PATTERNS = (
    "record-detail",
    "cloud-record",
    "minutes",
    "transcript",
    "timeline",
    "multi-record-file",
    "record-file",
    "subtitle",
    "download",
    "get-token",
)


class TmeetCdpDetailError(RuntimeError):
    """Raised when detail capture cannot continue."""


def now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def safe_slug(text: str, *, limit: int = 80) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|#\n\r\t]+", "-", text).strip(" .-")
    cleaned = re.sub(r"\s+", "-", cleaned)
    return cleaned[:limit] or "untitled"


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return sha256_text(text)


def should_capture(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if not (
        host.endswith("meeting.tencent.com")
        or host.endswith("tencentmeeting.com")
        or "wemeet" in host
    ):
        return False
    path = parsed.path.lower()
    return any(pattern in path for pattern in DETAIL_MATCH_PATTERNS)


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
    slug = safe_slug(urlparse(url).path.rstrip("/").rsplit("/", 1)[-1] or "response")
    return f"{index:03d}-{slug}.{ext}"


def load_record_list(wiki_path: Path, record_list_path: Path | None) -> list[dict[str, Any]]:
    if record_list_path:
        data = json.loads(record_list_path.read_text(encoding="utf-8"))
    else:
        latest = wiki_path / "_derived" / "tencent-meetings-cdp-record-list-latest.json"
        if not latest.exists():
            raise TmeetCdpDetailError(
                f"Missing record list manifest: {latest}. Run tmeet_capture_meeting_record_page_cdp.py first."
            )
        data = json.loads(latest.read_text(encoding="utf-8"))
    records = data.get("records") if isinstance(data, dict) else None
    if not isinstance(records, list):
        raise TmeetCdpDetailError("Record list JSON does not contain a `records` array.")
    return [record for record in records if isinstance(record, dict)]


def parse_record_date(record: dict[str, Any]) -> datetime | None:
    value = record.get("start_time")
    if value is not None and value != "":
        try:
            timestamp = int(str(value))
            if timestamp > 10_000_000_000:
                timestamp //= 1000
            return datetime.fromtimestamp(timestamp, ZoneInfo("Asia/Shanghai"))
        except ValueError:
            pass
    text = str(record.get("start_time_text") or "")
    if text:
        try:
            return datetime.strptime(text[:16], "%Y-%m-%d %H:%M").replace(
                tzinfo=ZoneInfo("Asia/Shanghai")
            )
        except ValueError:
            return None
    return None


def parse_boundary(value: str | None, *, end: bool = False) -> datetime | None:
    if not value:
        return None
    if "T" not in value and len(value) == 10:
        value = f"{value}T{'23:59:59' if end else '00:00:00'}"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return parsed.astimezone(ZoneInfo("Asia/Shanghai"))


def filter_records(
    records: list[dict[str, Any]],
    *,
    start: str | None = None,
    end: str | None = None,
    category: str | None = None,
) -> list[dict[str, Any]]:
    start_dt = parse_boundary(start)
    end_dt = parse_boundary(end, end=True)
    selected: list[dict[str, Any]] = []
    for record in records:
        if category and record.get("category") != category:
            continue
        record_dt = parse_record_date(record)
        if start_dt and record_dt and record_dt < start_dt:
            continue
        if end_dt and record_dt and record_dt > end_dt:
            continue
        selected.append(record)
    return selected


def detail_url(record: dict[str, Any]) -> str:
    jump_path = str(record.get("jump_path") or "")
    if jump_path:
        return urljoin("https://meeting.tencent.com", jump_path)
    record_id = record.get("record_id")
    if not record_id:
        raise TmeetCdpDetailError(f"Record has no jump_path or record_id: {record}")
    return (
        "https://meeting.tencent.com/user-center/shared-record-info?"
        f"id={record_id}&from=0&is-single=false&record_type=2"
    )


def parse_dom_assets(body_text: str) -> tuple[str, str]:
    marker = "内容由 AI 生成，仅供参考"
    if marker in body_text:
        minutes, transcript = body_text.split(marker, 1)
    else:
        minutes, transcript = body_text, ""

    minutes_start = minutes.find("智能总结")
    if minutes_start >= 0:
        minutes = minutes[minutes_start:]
    transcript = cleanup_transcript_tail(transcript.strip())
    return minutes.strip(), transcript.strip()


def ms_to_timestamp(value: Any) -> str:
    try:
        millis = int(value)
    except (TypeError, ValueError):
        return ""
    seconds = max(0, millis // 1000)
    hours, remainder = divmod(seconds, 3600)
    minutes, sec = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"


def seconds_to_timestamp(value: Any) -> str:
    try:
        seconds = max(0, int(float(value)))
    except (TypeError, ValueError):
        return ""
    hours, remainder = divmod(seconds, 3600)
    minutes, sec = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"


def extract_summary_from_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    data = payload.get("data")
    if not isinstance(data, dict):
        return ""
    summary = data.get("official_template_summary")
    if not isinstance(summary, dict):
        return ""
    infos = summary.get("summary_infos")
    if not isinstance(infos, list):
        return ""
    lines: list[str] = []
    for item in infos:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if isinstance(content, str) and content.strip():
            lines.append(content.strip())
    return "\n".join(lines).strip()


def transcript_from_minutes_payload(payload: Any) -> str:
    paragraphs = extract_minutes_paragraphs(payload)
    if not paragraphs:
        return ""
    lines: list[str] = []
    for paragraph in paragraphs:
        speaker = paragraph.get("speaker") if isinstance(paragraph.get("speaker"), dict) else {}
        speaker_name = speaker.get("user_name") or speaker.get("nickname") or "未知发言人"
        start = ms_to_timestamp(paragraph.get("start_time"))
        text_parts: list[str] = []
        sentences = paragraph.get("sentences")
        if isinstance(sentences, list):
            for sentence in sentences:
                if not isinstance(sentence, dict):
                    continue
                words = sentence.get("words")
                if isinstance(words, list):
                    text_parts.extend(
                        str(word.get("text") or "")
                        for word in words
                        if isinstance(word, dict) and word.get("text")
                    )
                elif sentence.get("text"):
                    text_parts.append(str(sentence.get("text")))
        text = "".join(text_parts).strip()
        if not text:
            continue
        prefix = f"**{start} {speaker_name}**" if start else f"**{speaker_name}**"
        lines.append(f"- {prefix}: {text}")
    return "\n".join(lines).strip()


def extract_minutes_paragraphs(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    minutes = payload.get("minutes")
    if not isinstance(minutes, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            minutes = data.get("minutes")
    if not isinstance(minutes, dict):
        return []
    paragraphs = minutes.get("paragraphs")
    if not isinstance(paragraphs, list):
        return []
    return [item for item in paragraphs if isinstance(item, dict)]


def best_minutes_payload(payloads: list[Any]) -> Any:
    if not payloads:
        return None
    return max(payloads, key=lambda payload: len(extract_minutes_paragraphs(payload)))


def extract_timeline_items(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    timeline_info = data.get("timeline_info")
    if not isinstance(timeline_info, dict):
        return []
    infos = timeline_info.get("timeline_infos")
    if not isinstance(infos, list):
        return []
    items: list[dict[str, Any]] = []
    for item in infos:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or item.get("ori_content") or "").strip()
        if not content:
            continue
        start_time = item.get("start_time")
        items.append(
            {
                "id": item.get("id"),
                "start_time_seconds": start_time,
                "timestamp": seconds_to_timestamp(start_time),
                "content": content,
                "source": "query-timeline",
            }
        )
    return items


def best_timeline_payload(payloads: list[Any]) -> Any:
    if not payloads:
        return None
    return max(payloads, key=lambda payload: len(extract_timeline_items(payload)))


def timeline_markdown(title: str, record: dict[str, Any], items: list[dict[str, Any]]) -> str:
    lines = []
    for item in items:
        timestamp = item.get("timestamp") or "00:00"
        content = str(item.get("content") or "").strip()
        if content:
            lines.append(f"- **{timestamp}** {content}")
    return asset_markdown(
        f"{title} - Timeline",
        record,
        "\n".join(lines),
        "raw/tencent-meeting-timeline",
    )


def extract_video_file_metadata(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    files = data.get("files")
    if not isinstance(files, list):
        return []
    return [item for item in files if isinstance(item, dict)]


def best_video_file_payload(payloads: list[Any]) -> Any:
    if not payloads:
        return None
    return max(payloads, key=lambda payload: len(extract_video_file_metadata(payload)))


def redact_url(url: str) -> str:
    parts = urlsplit(url)
    if not parts.query:
        return url
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "<redacted>", parts.fragment))


def normalized_video_key(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def video_filename_from_url(url: str, index: int) -> str:
    basename = Path(urlsplit(url).path).name or f"video-{index}.mp4"
    basename = safe_slug(basename, limit=120)
    if not basename.lower().endswith(".mp4"):
        basename = f"{basename}.mp4"
    return f"{index:02d}-{basename}"


def infer_video_kind(url: str) -> str:
    path = urlsplit(url).path.lower()
    if "screen" in path:
        return "screen"
    if "speaker" in path:
        return "speaker"
    if "gallery" in path:
        return "gallery"
    if "recording" in path:
        return "recording"
    return "unknown"


def collect_video_sources(page: Any) -> list[dict[str, Any]]:
    try:
        page.locator("video").first.scroll_into_view_if_needed(timeout=2_000)
    except Exception:  # noqa: BLE001
        pass
    try:
        page.locator("video").first.evaluate(
            """video => {
                video.muted = true;
                const promise = video.play();
                if (promise && promise.catch) {
                    promise.catch(() => null);
                }
            }""",
            timeout=2_000,
        )
        page.wait_for_timeout(1_500)
    except Exception:  # noqa: BLE001
        pass
    try:
        raw_sources = page.evaluate(
            """() => {
                const sources = [];
                const push = (source, index, tag) => {
                    if (!source) return;
                    sources.push({
                        index,
                        tag,
                        src: source.src || source.currentSrc || source.href || source.name || "",
                        currentSrc: source.currentSrc || "",
                        type: source.type || "",
                        poster: source.poster || "",
                        duration: Number.isFinite(source.duration) ? source.duration : null,
                        readyState: source.readyState ?? null
                    });
                };
                [...document.querySelectorAll('video')].forEach((video, index) => {
                    push(video, index, 'video');
                    [...video.querySelectorAll('source')].forEach((source, subIndex) => {
                        push(source, Number(`${index}${subIndex}`), 'source');
                    });
                });
                performance.getEntriesByType('resource')
                    .filter(entry => String(entry.name || '').includes('.mp4'))
                    .forEach((entry, index) => {
                        sources.push({
                            index: 1000 + index,
                            tag: 'performance',
                            src: entry.name,
                            currentSrc: entry.name,
                            type: 'video/mp4',
                            poster: '',
                            duration: null,
                            readyState: null
                        });
                    });
                return sources;
            }"""
        )
    except Exception:  # noqa: BLE001
        return []
    seen: set[str] = set()
    sources: list[dict[str, Any]] = []
    for item in raw_sources:
        if not isinstance(item, dict):
            continue
        url = str(item.get("currentSrc") or item.get("src") or "").strip()
        if not url or ".mp4" not in url.lower():
            continue
        key = normalized_video_key(url)
        if key in seen:
            continue
        seen.add(key)
        sources.append(
            {
                "kind": infer_video_kind(url),
                "url_redacted": redact_url(url),
                "normalized_url_redacted": redact_url(key),
                "dom_tag": item.get("tag"),
                "type": item.get("type") or "video/mp4",
                "duration": item.get("duration"),
                "ready_state": item.get("readyState"),
                "_download_url": url,
            }
        )
    return sources


def download_video_sources(
    sources: list[dict[str, Any]],
    *,
    video_dir: Path,
    referer: str,
    skip_existing_videos: bool,
) -> list[dict[str, Any]]:
    video_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for index, source in enumerate(sources, start=1):
        url = str(source.get("_download_url") or "")
        filename = video_filename_from_url(url, index)
        target = video_dir / filename
        result = {key: value for key, value in source.items() if not key.startswith("_")}
        result["filename"] = filename
        result["path"] = str(target)
        if skip_existing_videos and target.exists() and target.stat().st_size > 0:
            result.update(
                {
                    "download_status": "skipped_existing",
                    "bytes": target.stat().st_size,
                    "sha256": sha256_file(target),
                }
            )
            results.append(result)
            continue
        tmp = target.with_suffix(target.suffix + ".part")
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/125.0.0.0 Safari/537.36"
                    ),
                    "Referer": referer,
                    "Accept": "video/mp4,video/*,*/*;q=0.8",
                },
            )
            with urlopen(request, timeout=90) as response, tmp.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
            tmp.replace(target)
            result.update(
                {
                    "download_status": "downloaded",
                    "bytes": target.stat().st_size,
                    "sha256": sha256_file(target),
                }
            )
        except Exception as exc:  # noqa: BLE001
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            result.update({"download_status": "error", "error": str(exc)})
        results.append(result)
    return results


def visible_text_rect(page: Any, text: str) -> dict[str, float] | None:
    try:
        rect = page.evaluate(
            """text => {
                const candidates = [...document.querySelectorAll('li, button, span, div')]
                    .map(el => {
                        const raw = (el.innerText || el.textContent || '').trim();
                        const r = el.getBoundingClientRect();
                        return {text: raw, x: r.x, y: r.y, width: r.width, height: r.height};
                    })
                    .filter(item => item.text === text && item.width > 0 && item.height > 0);
                candidates.sort((a, b) => (a.width * a.height) - (b.width * b.height));
                return candidates[0] || null;
            }""",
            text,
        )
    except Exception:  # noqa: BLE001
        return None
    return rect if isinstance(rect, dict) else None


def click_rect_center(page: Any, rect: dict[str, float]) -> None:
    page.mouse.click(float(rect["x"]) + float(rect["width"]) / 2, float(rect["y"]) + float(rect["height"]) / 2)


def hover_rect_center(page: Any, rect: dict[str, float]) -> None:
    page.mouse.move(float(rect["x"]) + float(rect["width"]) / 2, float(rect["y"]) + float(rect["height"]) / 2)


def extract_official_video_download_links(payloads: list[Any]) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    if not payloads:
        return links
    payload = payloads[-1]
    if not isinstance(payload, dict):
        return links
    top_links = payload.get("links")
    if not isinstance(top_links, list):
        return links
    for item in top_links:
        if not isinstance(item, dict):
            continue
        link = str(item.get("link") or "").strip()
        if link:
            links.append(
                {
                    "kind": "recording",
                    "filename": str(item.get("filename") or Path(urlsplit(link).path).name or "recording.mp4"),
                    "url": link,
                    "url_redacted": redact_url(link),
                    "source": "download-meeting",
                }
            )
        streams = item.get("multi_stream_recordings")
        if isinstance(streams, list):
            for stream in streams:
                if not isinstance(stream, dict):
                    continue
                stream_link = str(stream.get("link") or "").strip()
                if not stream_link:
                    continue
                stream_type = stream.get("stream_type")
                kind = "speaker" if stream_type == 2 else "screen" if stream_type == 1 else f"stream-{stream_type}"
                links.append(
                    {
                        "kind": kind,
                        "stream_type": stream_type,
                        "filename": str(
                            stream.get("filename")
                            or Path(urlsplit(stream_link).path).name
                            or f"{kind}.mp4"
                        ),
                        "url": stream_link,
                        "url_redacted": redact_url(stream_link),
                        "source": "download-meeting.multi_stream_recordings",
                    }
                )
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in links:
        key = normalized_video_key(str(item.get("url") or ""))
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def fetch_official_video_download_payload(page: Any, record: dict[str, Any]) -> Any:
    file_id = str(record.get("file_id") or record.get("uni_record_id") or "").strip()
    if not file_id:
        raise TmeetCdpDetailError("Record has no file_id/uni_record_id for Tencent Meeting video download.")
    return page.evaluate(
        """async ({fileId}) => {
            const findCorpId = () => {
                for (const entry of performance.getEntriesByType('resource')) {
                    try {
                        const url = new URL(entry.name);
                        const value = url.searchParams.get('c_account_corp_id');
                        if (value) return value;
                    } catch (_) {}
                }
                return '';
            };
            const nonce = Math.random().toString(36).slice(2, 11);
            const params = new URLSearchParams({
                c_app_id: '',
                c_os_model: 'web',
                c_os: 'web',
                c_os_version: navigator.userAgent,
                c_timestamp: String(Math.floor(Date.now() / 1000)),
                c_nonce: nonce,
                c_app_version: '',
                c_instance_id: '5',
                rnds: nonce,
                c_district: '0',
                platform: 'Web',
                c_app_uid: '',
                c_account_corp_id: findCorpId(),
                'trace-id': crypto.randomUUID().replaceAll('-', ''),
                id: fileId,
                pwd: '',
                source: 'owner',
                activity_uid: '',
                tk: '',
                need_multi_stream: '1',
                from_share: '1',
                enter_from: 'share',
                c_lang: 'zh-CN'
            });
            const url = 'https://meeting.tencent.com/wemeet-cloudrecording-webapi/v1/download/meeting?' + params.toString();
            const response = await fetch(url, {credentials: 'include'});
            const text = await response.text();
            let payload = null;
            try {
                payload = JSON.parse(text);
            } catch (_) {}
            return {
                status: response.status,
                content_type: response.headers.get('content-type') || '',
                url,
                payload,
                text: payload ? '' : text.slice(0, 1000)
            };
        }""",
        {"fileId": file_id},
    )


def save_download(download: Any, target: Path, *, skip_existing_videos: bool) -> dict[str, Any]:
    target.parent.mkdir(parents=True, exist_ok=True)
    if skip_existing_videos and target.exists() and target.stat().st_size > 0:
        return {
            "download_status": "skipped_existing",
            "path": str(target),
            "bytes": target.stat().st_size,
            "sha256": sha256_file(target),
        }
    download.save_as(str(target))
    return {
        "download_status": "downloaded",
        "path": str(target),
        "bytes": target.stat().st_size,
        "sha256": sha256_file(target),
    }


def anchor_download_video(
    page: Any,
    *,
    url: str,
    filename: str,
    target: Path,
    skip_existing_videos: bool,
) -> dict[str, Any]:
    if skip_existing_videos and target.exists() and target.stat().st_size > 0:
        return {
            "download_status": "skipped_existing",
            "path": str(target),
            "bytes": target.stat().st_size,
            "sha256": sha256_file(target),
        }
    with page.expect_download(timeout=180_000) as download_info:
        page.evaluate(
            """({url, filename}) => {
                const anchor = document.createElement('a');
                anchor.href = url;
                anchor.download = filename;
                anchor.style.display = 'none';
                document.body.appendChild(anchor);
                anchor.click();
                setTimeout(() => anchor.remove(), 1000);
            }""",
            {"url": url, "filename": filename},
        )
    download = download_info.value
    return save_download(download, target, skip_existing_videos=skip_existing_videos)


def download_official_video_assets(
    page: Any,
    *,
    video_dir: Path,
    record: dict[str, Any],
    download_payloads: list[Any],
    skip_existing_videos: bool,
) -> list[dict[str, Any]]:
    video_dir.mkdir(parents=True, exist_ok=True)
    try:
        fetched = fetch_official_video_download_payload(page, record)
        if isinstance(fetched, dict) and isinstance(fetched.get("payload"), dict):
            download_payloads.append(fetched["payload"])
            write_json(video_dir / "download_meeting_payload.json", fetched["payload"])
        elif isinstance(fetched, dict):
            write_json(video_dir / "download_meeting_error.json", fetched)
    except Exception as exc:  # noqa: BLE001
        write_json(video_dir / "download_meeting_error.json", {"error": str(exc)})

    official_links = extract_official_video_download_links(download_payloads)
    if official_links:
        results: list[dict[str, Any]] = []
        for index, link in enumerate(official_links, start=1):
            url = str(link.get("url") or "")
            filename = safe_slug(str(link.get("filename") or Path(urlsplit(url).path).name), limit=160)
            if not filename.lower().endswith(".mp4"):
                filename += ".mp4"
            target = video_dir / f"{index:02d}-{filename}"
            item = {
                "kind": link.get("kind"),
                "stream_type": link.get("stream_type"),
                "filename": filename,
                "url_redacted": link.get("url_redacted"),
                "source": link.get("source"),
            }
            try:
                item.update(
                    anchor_download_video(
                        page,
                        url=url,
                        filename=filename,
                        target=target,
                        skip_existing_videos=skip_existing_videos,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                item.update({"download_status": "error", "error": str(exc), "path": str(target)})
            results.append(item)
        return results

    try:
        page.get_by_text("另存为", exact=True).click(timeout=5_000)
        page.wait_for_timeout(300)
        local_rect = visible_text_rect(page, "下载至本地")
        if not local_rect:
            raise TmeetCdpDetailError("Cannot find Tencent Meeting `下载至本地` menu item.")
        hover_rect_center(page, local_rect)
        page.wait_for_timeout(500)
        video_rect = visible_text_rect(page, "视频内容")
        if not video_rect:
            raise TmeetCdpDetailError("Cannot find Tencent Meeting `视频内容` download menu item.")
        with page.expect_download(timeout=180_000) as download_info:
            click_rect_center(page, video_rect)
        first_download = download_info.value
        page.wait_for_timeout(800)
    except Exception as exc:  # noqa: BLE001
        return [{"download_status": "error", "error": str(exc), "source": "ui-menu"}]

    results: list[dict[str, Any]] = []
    first_filename = safe_slug(first_download.suggested_filename or "video-content.mp4", limit=160)
    if not first_filename.lower().endswith(".mp4"):
        first_filename += ".mp4"
    first_target = video_dir / f"01-{first_filename}"
    first_result = {
        "kind": "recording",
        "filename": first_filename,
        "suggested_filename": first_download.suggested_filename,
        "download_url_redacted": redact_url(first_download.url),
        "source": "ui-menu.video-content",
    }
    first_result.update(save_download(first_download, first_target, skip_existing_videos=skip_existing_videos))
    results.append(first_result)

    return results


def video_files_markdown(title: str, record: dict[str, Any], results: list[dict[str, Any]]) -> str:
    lines = ["| # | Kind | Status | Bytes | File |", "|---:|---|---|---:|---|"]
    for index, item in enumerate(results, start=1):
        path = Path(str(item.get("path") or ""))
        filename = path.name if path.name else str(item.get("filename") or "")
        lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    str(item.get("kind") or ""),
                    str(item.get("download_status") or "found"),
                    str(item.get("bytes") or 0),
                    f"`videos/{filename}`" if filename else "",
                ]
            )
            + " |"
        )
    return asset_markdown(
        f"{title} - Video Files",
        record,
        "\n".join(lines),
        "raw/tencent-meeting-video-files",
    )


def cleanup_transcript_tail(text: str) -> str:
    tail_markers = (
        "\n播放视频",
        "\n一张图看懂本场会议",
        "\n分发言人观点总结会议",
        "\n提取会议待办",
        "\n意见反馈",
    )
    result = text
    for marker in tail_markers:
        idx = result.find(marker)
        if idx >= 0:
            result = result[:idx]
    return result.strip()


def markdown_frontmatter(items: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in items.items():
        if isinstance(value, list):
            rendered = "[" + ", ".join(json.dumps(v, ensure_ascii=False) for v in value) + "]"
        elif isinstance(value, bool):
            rendered = "true" if value else "false"
        elif value is None:
            rendered = "null"
        else:
            rendered = json.dumps(value, ensure_ascii=False)
        lines.append(f"{key}: {rendered}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def source_markdown(record: dict[str, Any], report: dict[str, Any]) -> str:
    frontmatter = markdown_frontmatter(
        {
            "title": record.get("title") or "Tencent Meeting Detail",
            "type": "raw/tencent-meeting-detail-cdp",
            "category": record.get("category"),
            "source_url": report.get("final_url") or report.get("detail_url"),
            "captured_at": report.get("captured_at"),
            "record_id": record.get("record_id"),
            "uni_record_id": record.get("uni_record_id"),
            "meeting_id": record.get("meeting_id"),
            "meeting_code": record.get("meeting_code"),
            "sha256": report.get("sha256"),
        }
    )
    body = [
        f"# {record.get('title') or 'Tencent Meeting Detail'}",
        "",
        "## Assets",
        "",
        "- DOM snapshot: [[dom.md]]",
        "- Smart minutes: [[smart_minutes.md]]",
        "- Timeline: [[timeline.md]]",
        "- Transcript: [[transcript.md]]",
        "- Video files: [[video_files.md]]",
        "- Video source manifest: [[video_sources.json]]",
        "- Videos: `videos/`",
        "- Responses: `responses/`",
        "- Report: [[detail_report.json]]",
        "",
        "## Notes",
        "",
        "- Captured with `scripts/tmeet_capture_record_details_cdp.py`.",
        "- This page is raw material for llm-wiki; derived concepts should cite this source.",
    ]
    return frontmatter + "\n".join(body) + "\n"


def asset_markdown(title: str, record: dict[str, Any], text: str, asset_type: str) -> str:
    return markdown_frontmatter(
        {
            "title": title,
            "type": asset_type,
            "record_id": record.get("record_id"),
            "meeting_id": record.get("meeting_id"),
            "meeting_code": record.get("meeting_code"),
            "sha256": sha256_text(text),
        }
    ) + f"# {title}\n\n{text.strip()}\n"


def capture_one_detail(
    *,
    page: Any,
    record: dict[str, Any],
    output_root: Path,
    wait_ms: int,
    download_videos: bool,
    skip_existing_videos: bool,
) -> dict[str, Any]:
    captured_at = now_iso()
    title = str(record.get("title") or "untitled")
    record_id = str(record.get("record_id") or record.get("uni_record_id") or sha256_json(record))
    folder = output_root / f"{safe_slug(record.get('start_time_text') or '')}-{safe_slug(title)}-{safe_slug(record_id)[:16]}"
    response_dir = folder / "responses"
    response_dir.mkdir(parents=True, exist_ok=True)

    responses: list[dict[str, Any]] = []
    summary_payloads: list[Any] = []
    minutes_payloads: list[Any] = []
    timeline_payloads: list[Any] = []
    video_file_payloads: list[Any] = []
    download_payloads: list[Any] = []

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
            responses.append({"url": url, "status": getattr(response, "status", None), "error": str(exc)})
            return

        parsed = maybe_json(text, content_type)
        filename = response_filename(len(responses) + 1, url, content_type)
        path = response_dir / filename
        if parsed is not None:
            write_json(path, parsed)
            if "query-summary-and-note" in url:
                summary_payloads.append(parsed)
            if "minutes/detail" in url or extract_minutes_paragraphs(parsed):
                minutes_payloads.append(parsed)
            if "query-timeline" in url or extract_timeline_items(parsed):
                timeline_payloads.append(parsed)
            if "get-multi-record-file" in url or extract_video_file_metadata(parsed):
                video_file_payloads.append(parsed)
            if "wemeet-cloudrecording-webapi/v1/download/meeting" in url:
                download_payloads.append(parsed)
        else:
            write_text(path, text[:1_000_000])
        responses.append(
            {
                "url": url,
                "status": status,
                "method": response.request.method,
                "request_post_data": request_post_data,
                "content_type": content_type,
                "body_file": str(path.relative_to(folder)),
                "body_sha256": sha256_text(text),
                "body_size": len(text.encode("utf-8", errors="ignore")),
                "is_json": parsed is not None,
            }
        )

    page.on("response", on_response)
    target_url = detail_url(record)
    page.goto(target_url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(wait_ms)
    video_sources = collect_video_sources(page)
    final_url = page.url
    page_title = page.title()
    try:
        body_text = page.locator("body").inner_text(timeout=10_000)
    except Exception:  # noqa: BLE001
        body_text = ""

    dom_minutes, dom_transcript = parse_dom_assets(body_text)
    smart_minutes = next(
        (text for text in (extract_summary_from_payload(payload) for payload in summary_payloads) if text),
        dom_minutes,
    )
    minutes_payload = best_minutes_payload(minutes_payloads)
    transcript = transcript_from_minutes_payload(minutes_payload) or dom_transcript
    timeline_payload = best_timeline_payload(timeline_payloads)
    timeline_items = extract_timeline_items(timeline_payload)
    video_file_payload = best_video_file_payload(video_file_payloads)
    video_file_metadata = extract_video_file_metadata(video_file_payload)
    if download_videos:
        video_results = download_official_video_assets(
            page,
            video_dir=folder / "videos",
            record=record,
            download_payloads=download_payloads,
            skip_existing_videos=skip_existing_videos,
        )
    else:
        video_results = [{key: value for key, value in source.items() if not key.startswith("_")} for source in video_sources]
    page.remove_listener("response", on_response)
    report_seed = {
        "record": record,
        "final_url": final_url,
        "body_sha256": sha256_text(body_text),
        "responses": responses,
        "timeline_items": timeline_items,
        "video_sources": [
            {key: value for key, value in source.items() if not key.startswith("_")}
            for source in video_sources
        ],
    }
    report = {
        "success": True,
        "captured_at": captured_at,
        "record": record,
        "detail_url": target_url,
        "final_url": final_url,
        "page_title": page_title,
        "output_dir": str(folder),
        "response_count": len(responses),
        "json_response_count": sum(1 for item in responses if item.get("is_json")),
        "dom_char_count": len(body_text),
        "smart_minutes_char_count": len(smart_minutes),
        "transcript_char_count": len(transcript),
        "timeline_item_count": len(timeline_items),
        "video_source_count": len(video_sources),
        "video_downloaded_count": sum(
            1
            for item in video_results
            if item.get("download_status") in {"downloaded", "skipped_existing"}
        ),
        "video_file_metadata_count": len(video_file_metadata),
        "minutes_payload_count": len(minutes_payloads),
        "best_minutes_paragraph_count": len(extract_minutes_paragraphs(minutes_payload)),
        "timeline_payload_count": len(timeline_payloads),
        "video_file_payload_count": len(video_file_payloads),
        "download_payload_count": len(download_payloads),
        "sha256": sha256_json(report_seed),
        "warnings": [],
    }
    if "login.html" in final_url:
        report["warnings"].append("Detail page redirected to login; Chrome web session is not valid.")
    if not transcript:
        report["warnings"].append("No transcript text parsed from DOM.")
    if not smart_minutes:
        report["warnings"].append("No smart minutes text parsed from DOM.")
    if not timeline_items:
        report["warnings"].append("No timeline text parsed from page responses.")
    if download_videos and not video_sources:
        report["warnings"].append("No video sources found in DOM.")
    if download_videos:
        for item in video_results:
            if item.get("download_status") == "error":
                report["warnings"].append(
                    f"Video download failed: {item.get('filename')}: {item.get('error')}"
                )

    write_text(
        folder / "dom.md",
        f"# DOM Snapshot - {title}\n\n- URL: {final_url}\n- Captured: {captured_at}\n\n```text\n{body_text}\n```\n",
    )
    write_text(
        folder / "smart_minutes.md",
        asset_markdown(f"{title} - Smart Minutes", record, smart_minutes, "raw/tencent-meeting-smart-minutes"),
    )
    write_text(
        folder / "transcript.md",
        asset_markdown(f"{title} - Transcript", record, transcript, "raw/tencent-meeting-transcript"),
    )
    write_json(
        folder / "timeline.json",
        {
            "record_id": record.get("record_id"),
            "meeting_id": record.get("meeting_id"),
            "items": timeline_items,
        },
    )
    write_text(folder / "timeline.md", timeline_markdown(title, record, timeline_items))
    write_json(
        folder / "video_sources.json",
        {
            "record_id": record.get("record_id"),
            "meeting_id": record.get("meeting_id"),
            "download_videos": download_videos,
            "download_payload_count": len(download_payloads),
            "video_file_metadata": video_file_metadata,
            "sources": video_results,
        },
    )
    write_text(folder / "video_files.md", video_files_markdown(title, record, video_results))
    write_json(folder / "detail_report.json", report)
    write_text(folder / "source.md", source_markdown(record, report))
    return report


def capture_record_details(
    *,
    cdp_url: str,
    wiki_path: Path,
    record_list_path: Path | None = None,
    output_root: Path | None = None,
    limit: int = 1,
    offset: int = 0,
    wait_ms: int = 10_000,
    start: str | None = None,
    end: str | None = None,
    category: str | None = None,
    download_videos: bool = False,
    skip_existing_videos: bool = True,
) -> dict[str, Any]:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        raise TmeetCdpDetailError(
            "Playwright is required for Chrome CDP capture. Install with `pip install playwright`."
        ) from exc

    records = load_record_list(wiki_path, record_list_path)
    filtered_records = filter_records(records, start=start, end=end, category=category)
    selected = filtered_records[offset:] if limit <= 0 else filtered_records[offset : offset + limit]
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S")
    root = output_root or wiki_path / "raw" / "tencent-meetings" / "cdp-details" / stamp
    root.mkdir(parents=True, exist_ok=True)
    reports: list[dict[str, Any]] = []

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(cdp_url)
        except PlaywrightError as exc:
            raise TmeetCdpDetailError(
                f"Cannot connect to Chrome CDP at {cdp_url}. Start Chrome with remote debugging first."
            ) from exc
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        for index, record in enumerate(selected, start=1):
            print(
                f"[tmeet-cdp-detail] {index}/{len(selected)} {record.get('start_time_text', '')} {record.get('title', '')}",
                file=sys.stderr,
                flush=True,
            )
            try:
                reports.append(
                    capture_one_detail(
                        page=page,
                        record=record,
                        output_root=root,
                        wait_ms=wait_ms,
                        download_videos=download_videos,
                        skip_existing_videos=skip_existing_videos,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                reports.append(
                    {
                        "success": False,
                        "record": record,
                        "error": str(exc),
                        "warnings": [str(exc)],
                    }
                )
        page.close()

    manifest = {
        "success": True,
        "captured_at": now_iso(),
        "source_record_count": len(records),
        "filtered_record_count": len(filtered_records),
        "offset": offset,
        "limit": limit,
        "start": start,
        "end": end,
        "category": category,
        "download_videos": download_videos,
        "skip_existing_videos": skip_existing_videos,
        "attempted_count": len(selected),
        "exported_count": sum(1 for report in reports if report.get("success")),
        "timeline_item_count": sum(int(report.get("timeline_item_count") or 0) for report in reports),
        "video_source_count": sum(int(report.get("video_source_count") or 0) for report in reports),
        "video_downloaded_count": sum(int(report.get("video_downloaded_count") or 0) for report in reports),
        "output_root": str(root),
        "reports": reports,
        "warnings": [
            warning
            for report in reports
            for warning in report.get("warnings", [])
        ],
    }
    write_json(root / "detail_manifest.json", manifest)
    write_text(root / "detail_manifest.md", detail_manifest_markdown(manifest))
    write_json(wiki_path / "_derived" / "tencent-meetings-cdp-details-latest.json", manifest)
    return manifest


def detail_manifest_markdown(manifest: dict[str, Any]) -> str:
    lines = [
        "---",
        'title: "Tencent Meeting CDP Detail Manifest"',
        'type: "raw/tencent-meeting-detail-manifest"',
        f"captured_at: {json.dumps(manifest['captured_at'], ensure_ascii=False)}",
        f"exported_count: {manifest['exported_count']}",
        f"attempted_count: {manifest['attempted_count']}",
        "---",
        "",
        "# Tencent Meeting CDP Detail Manifest",
        "",
        f"- Output root: `{manifest['output_root']}`",
        f"- Attempted: {manifest['attempted_count']}",
        f"- Exported: {manifest['exported_count']}",
        f"- Timeline items: {manifest.get('timeline_item_count', 0)}",
        f"- Video sources: {manifest.get('video_source_count', 0)}",
        f"- Videos downloaded/skipped: {manifest.get('video_downloaded_count', 0)}",
        "",
        "| # | Status | Title | Minutes chars | Transcript chars | Timeline | Videos | Path |",
        "|---:|---|---|---:|---:|---:|---:|---|",
    ]
    for index, report in enumerate(manifest.get("reports", []), start=1):
        record = report.get("record") if isinstance(report.get("record"), dict) else {}
        title = str(record.get("title") or "").replace("|", "\\|")
        lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    "ok" if report.get("success") else "error",
                    title,
                    str(report.get("smart_minutes_char_count") or 0),
                    str(report.get("transcript_char_count") or 0),
                    str(report.get("timeline_item_count") or 0),
                    str(report.get("video_downloaded_count") or report.get("video_source_count") or 0),
                    str(report.get("output_dir") or ""),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture Tencent Meeting record detail pages through Chrome CDP."
    )
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    parser.add_argument("--wiki-path", type=Path, default=DEFAULT_WIKI_PATH)
    parser.add_argument("--record-list-path", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=1, help="Number of records to capture. Use 0 for all.")
    parser.add_argument("--wait-ms", type=int, default=10_000)
    parser.add_argument("--start", help="Optional start date or ISO datetime, e.g. 2026-05-01.")
    parser.add_argument("--end", help="Optional end date or ISO datetime, e.g. 2026-05-23.")
    parser.add_argument("--category", choices=["coaching", "courses", "unknown"])
    parser.add_argument(
        "--download-videos",
        action="store_true",
        help="Download MP4 sources exposed by the record detail page into each record's videos/ folder.",
    )
    parser.add_argument(
        "--no-skip-existing-videos",
        action="store_true",
        help="Re-download MP4 files even when a local file already exists.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = capture_record_details(
            cdp_url=args.cdp_url,
            wiki_path=args.wiki_path,
            record_list_path=args.record_list_path,
            output_root=args.output_root,
            limit=args.limit,
            offset=args.offset,
            wait_ms=args.wait_ms,
            start=args.start,
            end=args.end,
            category=args.category,
            download_videos=args.download_videos,
            skip_existing_videos=not args.no_skip_existing_videos,
        )
    except Exception as exc:  # noqa: BLE001
        if args.json:
            print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Detail capture written to {manifest['output_root']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
