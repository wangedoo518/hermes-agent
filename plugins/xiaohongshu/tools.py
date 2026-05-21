"""Single-note Xiaohongshu note extraction tool."""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from hermes_constants import get_hermes_dir
from tools.registry import tool_error, tool_result

logger = logging.getLogger(__name__)

XHS_EXTRACT_NOTE_SCHEMA = {
    "name": "xhs_extract_note",
    "description": (
        "Extract one user-submitted Xiaohongshu note URL. Resolves short/long "
        "links, extracts note_id/title/body/images/video metadata, downloads "
        "media into the current Hermes cache by default, optionally OCRs images, "
        "transcribes video audio by default, and writes note.json plus note.md."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "A Xiaohongshu/xhslink URL, or share text containing one URL.",
            },
            "ocr": {
                "type": "boolean",
                "description": "Run OCR on downloaded note images using Hermes vision.",
                "default": True,
            },
            "max_images": {
                "type": "integer",
                "description": "Maximum images to download and OCR from the note.",
                "default": 9,
                "minimum": 1,
                "maximum": 18,
            },
            "vision_model": {
                "type": "string",
                "description": "Optional override for the Hermes vision model used for OCR.",
            },
            "download_video": {
                "type": "boolean",
                "description": "Download public video assets into the Hermes cache.",
                "default": True,
            },
            "transcribe": {
                "type": "boolean",
                "description": "Extract video audio and run Hermes STT.",
                "default": True,
            },
            "stt_model": {
                "type": "string",
                "description": "Optional override for the Hermes STT model used for video transcription.",
            },
            "stt_language": {
                "type": "string",
                "description": "Language hint for video transcription. Defaults to Chinese for Xiaohongshu videos.",
                "default": "zh",
            },
            "max_video_mb": {
                "type": "integer",
                "description": "Maximum video download size in MiB.",
                "default": 100,
                "minimum": 1,
                "maximum": 500,
            },
            "extract_comments": {
                "type": "boolean",
                "description": (
                    "Extract comments from the logged-in Browser/CDP DOM."
                ),
                "default": True,
            },
            "max_comments": {
                "type": "integer",
                "description": "Maximum top-level comments to keep in note.json/note.md. Defaults high enough for full extraction on typical notes.",
                "default": 1000,
                "minimum": 0,
                "maximum": 1000,
            },
        },
        "required": ["url"],
    },
}


_URL_RE = re.compile(r"https?://[^\s<>'\"`，。；！？）】》]+", re.IGNORECASE)
_NOTE_ID_RE = re.compile(r"(?<![a-f0-9])([a-f0-9]{24})(?![a-f0-9])", re.IGNORECASE)
_SCRIPT_RE = re.compile(r"<script\b[^>]*>(.*?)</script\s*>", re.IGNORECASE | re.DOTALL)
_JSON_LD_RE = re.compile(
    r"<script\b[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script\s*>",
    re.IGNORECASE | re.DOTALL,
)
_META_RE = re.compile(r"<meta\b([^>]*)>", re.IGNORECASE | re.DOTALL)
_ATTR_RE = re.compile(r"([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*([\"'])(.*?)\2", re.DOTALL)
_TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title\s*>", re.IGNORECASE | re.DOTALL)
_IMG_URL_RE = re.compile(r"https?://[^\s<>'\"`，。；！？）】》]+", re.IGNORECASE)
_MAX_VIDEO_MB_DEFAULT = 100
_VIDEO_EXTENSIONS = (".mp4", ".m4v", ".mov", ".webm", ".m3u8")
_SUBTITLE_EXTENSIONS = (".srt", ".vtt")
_DEFAULT_XHS_STT_LANGUAGE = "zh"

_INITIAL_STATE_MARKERS = (
    "window.__INITIAL_STATE__",
    "__INITIAL_STATE__",
    "window.__NUXT__",
    "__NUXT__",
)
_ALLOWED_NOTE_HOSTS = {
    "xhslink.com",
    "www.xhslink.com",
    "xhs.cn",
    "www.xhs.cn",
}
_IMAGE_HOST_HINTS = (
    "xhscdn.com",
    "xiaohongshu.com",
    "sns-img",
    "sns-webpic",
)
_VIDEO_HOST_HINTS = (
    "xhscdn.com",
    "xiaohongshu.com",
    "sns-video",
)
_SUBTITLE_HOST_HINTS = (
    "xhscdn.com",
    "sns-subtitle",
)


@dataclass
class ParsedVideo:
    source_url: str
    cover_url: str = ""
    duration_ms: int | None = None
    width: int | None = None
    height: int | None = None
    format: str = ""
    bitrate: int | None = None

    def to_record(self, index: int) -> dict[str, Any]:
        return {
            "index": index,
            "source_url": self.source_url,
            "cover_url": self.cover_url,
            "duration_ms": self.duration_ms,
            "width": self.width,
            "height": self.height,
            "format": self.format,
            "bitrate": self.bitrate,
        }


@dataclass
class ParsedSubtitle:
    source_url: str
    language_hint: str = ""
    format: str = "srt"

    def to_record(self, index: int) -> dict[str, Any]:
        return {
            "index": index,
            "source_url": self.source_url,
            "language_hint": self.language_hint,
            "format": self.format,
        }


@dataclass
class PageFetch:
    original_url: str
    final_url: str
    html_text: str = ""
    status_code: int | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class ParsedNote:
    note_id: str
    source_url: str
    resolved_url: str
    title: str = ""
    content: str = ""
    note_type: str = "unknown"
    author: dict[str, str] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)
    comment_threads: dict[str, Any] = field(default_factory=dict)
    image_urls: list[str] = field(default_factory=list)
    videos: list[ParsedVideo] = field(default_factory=list)
    subtitles: list[ParsedSubtitle] = field(default_factory=list)
    raw_metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _clean_url_candidate(value: str) -> str:
    return value.strip().rstrip(").,;!?，。；！？）】》\"'")


def _extract_xhs_url(text: str) -> str | None:
    for match in _URL_RE.findall(text or ""):
        candidate = _clean_url_candidate(match)
        if _is_allowed_xhs_url(candidate):
            return candidate
    return None


def _is_allowed_xhs_url(value: str) -> bool:
    try:
        host = (urlparse(value).hostname or "").lower()
    except Exception:
        return False
    return (
        host in _ALLOWED_NOTE_HOSTS
        or host == "xiaohongshu.com"
        or host.endswith(".xiaohongshu.com")
    )


def _extract_note_id_from_url(value: str) -> str | None:
    try:
        parsed = urlparse(value)
    except Exception:
        return None
    path_match = _NOTE_ID_RE.search(parsed.path or "")
    if path_match:
        return path_match.group(1).lower()
    query = parse_qs(parsed.query or "")
    for key in ("note_id", "noteId", "id"):
        for item in query.get(key, []):
            query_match = _NOTE_ID_RE.search(item)
            if query_match:
                return query_match.group(1).lower()
    any_match = _NOTE_ID_RE.search(value)
    return any_match.group(1).lower() if any_match else None


def _safe_note_id(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_")
    return cleaned[:80] or hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _xhs_cache_root() -> Path:
    root = get_hermes_dir("cache/xiaohongshu", "xhs_cache")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _meta_tags(html_text: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    for raw_attrs in _META_RE.findall(html_text or ""):
        attrs = {
            key.lower(): html.unescape(value.strip())
            for key, _quote, value in _ATTR_RE.findall(raw_attrs)
        }
        content = attrs.get("content")
        if not content:
            continue
        name = attrs.get("property") or attrs.get("name") or attrs.get("itemprop")
        if name:
            meta[name.lower()] = content
    return meta


def _html_title(html_text: str) -> str:
    match = _TITLE_RE.search(html_text or "")
    if not match:
        return ""
    return _clean_text(match.group(1))


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _clean_title(value: str) -> str:
    text = _clean_text(value)
    for suffix in (" - 小红书", "_小红书", " | 小红书", "-小红书"):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    return text


def _normalise_image_url(value: str, base_url: str = "") -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    url = html.unescape(value.strip())
    url = url.replace("\\/", "/").replace("\\u002F", "/")
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = urljoin(base_url, url)
    if not url.lower().startswith(("http://", "https://")):
        return None
    if not _is_likely_note_image_url(url):
        return None
    return url


def _is_likely_note_image_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    value_l = value.lower()
    image_exts = (".jpg", ".jpeg", ".png", ".webp", ".gif")
    if "sns-video" in host or "picasso-static" in host or "/fe-platform/" in path:
        return False
    if ");" in value_l or "background-" in value_l:
        return False
    if not path or path == "/":
        return False
    if not any(hint in host for hint in _IMAGE_HOST_HINTS):
        return False
    if any(token in path for token in (".js", ".css", ".woff", ".svg", ".srt")):
        return False
    if any(path.endswith(ext) for ext in _VIDEO_EXTENSIONS):
        return False
    if not any(hint in host for hint in ("sns-img", "sns-webpic")) and not any(path.endswith(ext) for ext in image_exts):
        return False
    return True


def _normalise_video_url(value: str, base_url: str = "") -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    url = html.unescape(value.strip())
    url = url.replace("\\/", "/").replace("\\u002F", "/")
    url = url.rstrip("\\\"'")
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = urljoin(base_url, url)
    if not url.lower().startswith(("http://", "https://")):
        return None
    if not _is_likely_note_video_url(url):
        return None
    return url


def _is_likely_note_video_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    query = (parsed.query or "").lower()
    if "sns-img" in host or "sns-webpic" in host:
        return False
    if not path or path == "/":
        return False
    if not any(hint in host for hint in _VIDEO_HOST_HINTS):
        return False
    if any(path.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".ico", ".js", ".css", ".woff", ".svg")):
        return False
    if any(path.endswith(ext) for ext in _VIDEO_EXTENSIONS):
        return True
    if "sns-video" in host and "/stream/" in path:
        return True
    return any(token in path or token in query for token in ("video", "stream", "m3u8", "mp4"))


def _normalise_subtitle_url(value: str, base_url: str = "") -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    url = html.unescape(value.strip())
    url = url.replace("\\/", "/").replace("\\u002F", "/")
    url = url.rstrip("\\\"'")
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = urljoin(base_url, url)
    if not url.lower().startswith(("http://", "https://")):
        return None
    if not _is_likely_note_subtitle_url(url):
        return None
    return url


def _is_likely_note_subtitle_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    if not any(hint in host for hint in _SUBTITLE_HOST_HINTS):
        return False
    return "/subtitle/" in path or any(path.endswith(ext) for ext in _SUBTITLE_EXTENSIONS)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _dedupe_subtitles(subtitles: list[ParsedSubtitle]) -> list[ParsedSubtitle]:
    seen: set[str] = set()
    result: list[ParsedSubtitle] = []
    for subtitle in subtitles:
        url = subtitle.source_url
        if not url or url in seen:
            continue
        seen.add(url)
        result.append(subtitle)
    return result


def _dedupe_videos(videos: list[ParsedVideo]) -> list[ParsedVideo]:
    seen: set[str] = set()
    result: list[ParsedVideo] = []
    for video in videos:
        if not video.source_url:
            continue
        key = _video_dedupe_key(video.source_url)
        if key in seen:
            existing = next(item for item in result if _video_dedupe_key(item.source_url) == key)
            _merge_video_metadata(existing, video)
            continue
        seen.add(key)
        result.append(video)
    return result


def _video_dedupe_key(url: str) -> str:
    parsed = urlparse(url.rstrip("\\\"'"))
    path = parsed.path.rstrip("\\\"'").lower()
    if path:
        return path
    return f"{(parsed.hostname or '').lower()}{path}"


def _merge_video_metadata(target: ParsedVideo, source: ParsedVideo) -> None:
    if not target.cover_url and source.cover_url:
        target.cover_url = source.cover_url
    if target.duration_ms is None and source.duration_ms is not None:
        target.duration_ms = source.duration_ms
    if target.width is None and source.width is not None:
        target.width = source.width
    if target.height is None and source.height is not None:
        target.height = source.height
    if not target.format and source.format:
        target.format = source.format
    if target.bitrate is None and source.bitrate is not None:
        target.bitrate = source.bitrate


def _parse_optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, str):
            cleaned = value.strip()
            if not cleaned:
                return None
            return int(float(cleaned))
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_count(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = _clean_text(value).replace(",", "").replace("，", "").strip()
    if not text:
        return None
    multiplier = 1
    if text.endswith(("万", "w", "W")):
        multiplier = 10000
        text = text[:-1].strip()
    elif text.endswith(("千", "k", "K")):
        multiplier = 1000
        text = text[:-1].strip()
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return int(float(match.group(0)) * multiplier)
    except ValueError:
        return None


def _parse_optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, str):
            cleaned = value.strip()
            if not cleaned:
                return None
            return float(cleaned)
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_node_int(node: dict[str, Any], keys: set[str]) -> int | None:
    for key, value in node.items():
        if str(key).lower().replace("_", "") in keys:
            parsed = _parse_optional_int(value)
            if parsed is not None:
                return parsed
    return None


def _extract_duration_ms(node: dict[str, Any]) -> int | None:
    for key, value in node.items():
        key_l = str(key).lower().replace("_", "")
        if "duration" not in key_l and key_l not in {"time", "length"}:
            continue
        parsed = _parse_optional_float(value)
        if parsed is None:
            continue
        if "ms" in key_l or parsed > 1000:
            return int(parsed)
        return int(parsed * 1000)
    return None


def _extract_node_cover_url(node: dict[str, Any], base_url: str) -> str:
    cover_key_markers = ("cover", "poster", "thumbnail", "firstframe", "first_frame")
    for key, value in node.items():
        key_l = str(key).lower()
        if not any(marker in key_l for marker in cover_key_markers):
            continue
        if isinstance(value, str):
            normalised = _normalise_image_url(value, base_url=base_url)
            if normalised:
                return normalised
        elif isinstance(value, (dict, list)):
            for child in _walk_json(value):
                for child_value in child.values():
                    if isinstance(child_value, str):
                        normalised = _normalise_image_url(child_value, base_url=base_url)
                        if normalised:
                            return normalised
    return ""


def _video_format_from_url(url: str) -> str:
    path = urlparse(url.rstrip("\\\"'")).path.lower()
    for ext in _VIDEO_EXTENSIONS:
        if path.endswith(ext):
            return ext.lstrip(".")
    return ""


def _subtitle_format_from_url(url: str) -> str:
    path = urlparse(url.rstrip("\\\"'")).path.lower()
    for ext in _SUBTITLE_EXTENSIONS:
        if path.endswith(ext):
            return ext.lstrip(".")
    return "srt"


def _build_video_candidate(url: str, node: dict[str, Any], base_url: str) -> ParsedVideo:
    return ParsedVideo(
        source_url=url,
        cover_url=_extract_node_cover_url(node, base_url=base_url),
        duration_ms=_extract_duration_ms(node),
        width=_extract_node_int(node, {"width", "w"}),
        height=_extract_node_int(node, {"height", "h"}),
        format=_video_format_from_url(url),
        bitrate=_extract_node_int(node, {"bitrate", "bitratebps"}),
    )


_STAT_ALIASES = {
    "like_count": {"likedcount", "likecount", "likes", "likednum", "likenum", "liked"},
    "collect_count": {
        "collectedcount",
        "collectcount",
        "collects",
        "collectednum",
        "collectnum",
        "favcount",
        "favoritecount",
        "starcount",
    },
    "comment_count": {"commentcount", "commentscount", "comments", "commentnum"},
    "share_count": {"sharecount", "shares", "sharenum"},
}


def _canonical_stat_key(key: Any) -> str | None:
    key_l = str(key).lower().replace("_", "").replace("-", "")
    for canonical, aliases in _STAT_ALIASES.items():
        if key_l in aliases:
            return canonical
    return None


def _extract_stats_from_node(node: dict[str, Any], source: str = "json") -> dict[str, Any] | None:
    stats: dict[str, Any] = {
        "like_count": None,
        "collect_count": None,
        "comment_count": None,
        "share_count": None,
        "liked": None,
        "collected": None,
        "source": source,
        "status": "missing",
    }
    matched = 0
    for key, value in node.items():
        canonical = _canonical_stat_key(key)
        if canonical:
            parsed = _parse_count(value)
            if parsed is not None:
                stats[canonical] = parsed
                matched += 1
            continue
        key_l = str(key).lower()
        if key_l == "liked" and isinstance(value, bool):
            stats["liked"] = value
        elif key_l == "collected" and isinstance(value, bool):
            stats["collected"] = value
    if matched == 0:
        return None
    stats["status"] = "ok"
    return stats


def _stats_score(stats: dict[str, Any]) -> tuple[int, int, int]:
    source = str(stats.get("source") or "").lower()
    completeness = sum(
        1
        for key in ("like_count", "collect_count", "comment_count", "share_count")
        if stats.get(key) is not None
    )
    source_bonus = 2 if "interact" in source else 0
    total = sum(
        int(stats.get(key) or 0)
        for key in ("like_count", "collect_count", "comment_count", "share_count")
    )
    return completeness, source_bonus, total


def _pick_stats(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        return {
            "status": "missing",
            "source": "",
            "like_count": None,
            "collect_count": None,
            "comment_count": None,
            "share_count": None,
            "liked": None,
            "collected": None,
        }
    return max(candidates, key=_stats_score)


def _extract_stats_from_text(html_text: str) -> dict[str, Any] | None:
    normalised = (html_text or "").replace("\\/", "/").replace("\\u002F", "/")
    match = re.search(r'"interactInfo"\s*:\s*(\{[^{}]{1,1200}\})', normalised)
    if not match:
        return None
    parsed = _json_loads_lenient(match.group(1))
    if isinstance(parsed, dict):
        return _extract_stats_from_node(parsed, source="regex:interactInfo")
    return None


_COMMENT_LIST_KEYS = {
    "list",
    "items",
    "comments",
    "commentlist",
    "commentlistvo",
    "subcomments",
    "subcommentlist",
    "replylist",
    "replies",
}
_COMMENT_TEXT_KEYS = {"content", "text", "comment", "commentcontent", "desc"}
_COMMENT_ID_KEYS = {"id", "commentid", "comment_id", "cid"}
_COMMENT_TIME_KEYS = {"createtime", "createtime", "create_time", "time", "timestamp", "date"}
_COMMENT_LIKE_KEYS = {"likecount", "likedcount", "likes", "like_count"}
_COMMENT_USER_KEYS = {"user", "userinfo", "author", "user_info"}


def _comment_empty(status: str = "missing", source: str = "") -> dict[str, Any]:
    return {
        "status": status,
        "source": source,
        "count": 0,
        "has_more": None,
        "cursor": "",
        "items": [],
    }


def _normalised_key(value: Any) -> str:
    return str(value).lower().replace("_", "").replace("-", "")


def _extract_comment_text(node: dict[str, Any]) -> str:
    for key, value in node.items():
        if _normalised_key(key) in _COMMENT_TEXT_KEYS and isinstance(value, str):
            text = _clean_text(value)
            if text:
                return text
    return ""


def _extract_comment_id(node: dict[str, Any]) -> str:
    for key, value in node.items():
        if _normalised_key(key) in _COMMENT_ID_KEYS and value is not None:
            text = _clean_text(value)
            if text:
                return text
    return ""


def _extract_comment_time(node: dict[str, Any]) -> str:
    for key, value in node.items():
        if _normalised_key(key) in _COMMENT_TIME_KEYS and value is not None:
            text = _clean_text(value)
            if text:
                return text
    return ""


def _extract_comment_like_count(node: dict[str, Any]) -> int | None:
    for key, value in node.items():
        if _normalised_key(key) in _COMMENT_LIKE_KEYS:
            parsed = _parse_count(value)
            if parsed is not None:
                return parsed
    return None


def _extract_comment_user(node: dict[str, Any]) -> dict[str, str]:
    candidates: list[dict[str, Any]] = []
    for key, value in node.items():
        if _normalised_key(key) in _COMMENT_USER_KEYS and isinstance(value, dict):
            candidates.append(value)
    candidates.append(node)
    for candidate in candidates:
        author: dict[str, str] = {}
        for key, value in candidate.items():
            key_n = _normalised_key(key)
            if key_n in {"userid", "user_id", "id", "userno"} and value is not None:
                author.setdefault("id", _clean_text(value))
            elif key_n in {"nickname", "nick", "name", "username"} and value is not None:
                author.setdefault("nickname", _clean_text(value))
            elif key_n in {"avatar", "image", "imageurl", "avatarurl"} and isinstance(value, str):
                author.setdefault("avatar", _clean_text(value))
        if author:
            return author
    return {}


def _extract_comment_ip_location(node: dict[str, Any]) -> str:
    for key, value in node.items():
        if _normalised_key(key) in {"iplocation", "iploc", "location"} and value is not None:
            text = _clean_text(value)
            if text:
                return text
    return ""


def _comment_reply_values(node: dict[str, Any]) -> list[Any]:
    values: list[Any] = []
    for key, value in node.items():
        key_n = _normalised_key(key)
        if key_n in _COMMENT_LIST_KEYS and "comment" in key_n:
            values.append(value)
        elif key_n in {"replylist", "replies"}:
            values.append(value)
    return values


def _parse_comment_node(node: Any, source: str, depth: int = 0) -> dict[str, Any] | None:
    if not isinstance(node, dict) or depth > 2:
        return None
    text = _extract_comment_text(node)
    if not text:
        return None
    record: dict[str, Any] = {
        "id": _extract_comment_id(node),
        "text": text,
        "author": _extract_comment_user(node),
        "like_count": _extract_comment_like_count(node),
        "time": _extract_comment_time(node),
        "ip_location": _extract_comment_ip_location(node),
        "source": source,
        "replies": [],
    }
    replies: list[dict[str, Any]] = []
    for value in _comment_reply_values(node):
        replies.extend(_parse_comment_items(value, f"{source}:replies", depth=depth + 1))
    record["replies"] = _dedupe_comment_records(replies)
    return record


def _parse_comment_items(value: Any, source: str, depth: int = 0) -> list[dict[str, Any]]:
    if isinstance(value, list):
        records: list[dict[str, Any]] = []
        for item in value:
            parsed = _parse_comment_node(item, source, depth=depth)
            if parsed:
                records.append(parsed)
        return records
    if isinstance(value, dict):
        direct = _parse_comment_node(value, source, depth=depth)
        if direct:
            return [direct]
        records: list[dict[str, Any]] = []
        for key, child in value.items():
            key_n = _normalised_key(key)
            if key_n in _COMMENT_LIST_KEYS and isinstance(child, (list, dict)):
                records.extend(_parse_comment_items(child, f"{source}:{key}", depth=depth))
        return records
    return []


def _extract_comment_threads_from_value(value: Any, source: str) -> dict[str, Any] | None:
    if not isinstance(value, (dict, list)):
        return None
    records = _parse_comment_items(value, source)
    has_more = None
    cursor = ""
    first_request_finish = None
    if isinstance(value, dict):
        for key, item in value.items():
            key_n = _normalised_key(key)
            if key_n == "hasmore" and isinstance(item, bool):
                has_more = item
            elif key_n == "cursor" and item is not None:
                cursor = _clean_text(item)
            elif key_n == "firstrequestfinish" and isinstance(item, bool):
                first_request_finish = item
    records = _dedupe_comment_records(records)
    if records:
        return {
            "status": "ok",
            "source": source,
            "count": len(records),
            "has_more": has_more,
            "cursor": cursor,
            "items": records,
        }
    if has_more and first_request_finish is False:
        return {
            "status": "lazy_unloaded",
            "source": source,
            "count": 0,
            "has_more": has_more,
            "cursor": cursor,
            "items": [],
        }
    return None


def _dedupe_comment_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for record in records:
        key = record.get("id") or f"{(record.get('author') or {}).get('nickname', '')}:{record.get('text', '')}"
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(record)
    return result


def _pick_comment_threads(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        return _comment_empty()
    ok_candidates = [item for item in candidates if item.get("items")]
    if ok_candidates:
        return max(ok_candidates, key=lambda item: len(item.get("items") or []))
    lazy_candidates = [item for item in candidates if item.get("status") == "lazy_unloaded"]
    if lazy_candidates:
        return lazy_candidates[0]
    return candidates[0]


def _limit_comment_threads(comment_threads: dict[str, Any], max_comments: int) -> dict[str, Any]:
    max_comments = max(0, min(int(max_comments or 0), 1000))
    threads = dict(comment_threads or _comment_empty())
    items = list(threads.get("items") or [])
    threads["items"] = items[:max_comments]
    threads["count"] = len(threads["items"])
    if threads.get("status") == "ok" and not threads["items"]:
        threads["status"] = "empty"
    return threads


def _comment_threads_need_api(comment_threads: dict[str, Any], stats: dict[str, Any]) -> bool:
    if (comment_threads or {}).get("items"):
        return False
    status = (comment_threads or {}).get("status")
    if status in {"lazy_unloaded", "missing", "empty"}:
        return True
    comment_count = (stats or {}).get("comment_count")
    return isinstance(comment_count, int) and comment_count > 0


def _cookie_records_to_header(value: Any) -> str:
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return ""
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return stripped
        return _cookie_records_to_header(parsed)

    if isinstance(value, dict):
        cookies = value.get("cookies")
        if isinstance(cookies, list):
            return _cookie_records_to_header(cookies)

        # Some XHS tools persist cookies as {"a1": "...", "web_session": "..."}.
        metadata_keys = {"version", "saved_at", "domain", "source", "browser", "expires", "expires_at"}
        pairs: list[str] = []
        for key, raw in value.items():
            if key in metadata_keys or raw is None or isinstance(raw, (dict, list)):
                continue
            name = str(key).strip()
            item_value = str(raw).strip()
            if name and item_value:
                pairs.append(f"{name}={item_value}")
        return "; ".join(pairs)

    if isinstance(value, list):
        pairs = []
        for item in value:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            raw = item.get("value")
            item_value = "" if raw is None else str(raw).strip()
            if name and item_value:
                pairs.append(f"{name}={item_value}")
        return "; ".join(pairs)

    return ""


def _xhs_cookie_from_file() -> str:
    path_value = (os.environ.get("HERMES_XHS_COOKIE_FILE") or os.environ.get("XHS_COOKIE_FILE") or "").strip()
    if not path_value:
        return ""
    cookie_path = Path(os.path.expanduser(path_value))
    try:
        return _cookie_records_to_header(cookie_path.read_text(encoding="utf-8"))
    except OSError as exc:
        logger.warning("Failed to read Xiaohongshu cookie file %s: %s", cookie_path, exc)
        return ""


def _xhs_cookie_from_env() -> str:
    inline_cookie = (os.environ.get("HERMES_XHS_COOKIE") or os.environ.get("XHS_COOKIE") or "").strip()
    if inline_cookie:
        return inline_cookie
    return _xhs_cookie_from_file()


def _comment_api_url(note: ParsedNote, cursor: str = "") -> str:
    query = parse_qs(urlparse(note.resolved_url or note.source_url).query or "")
    xsec_token = (query.get("xsec_token") or [""])[0]
    params = {
        "note_id": note.note_id,
        "cursor": cursor,
        "top_comment_id": "",
        "image_formats": "jpg,webp,avif",
    }
    if xsec_token:
        params["xsec_token"] = xsec_token
    encoded = urlencode(params)
    return f"https://edith.xiaohongshu.com/api/sns/web/v2/comment/page?{encoded}"


def _parse_comment_api_payload(data: dict[str, Any], source: str) -> dict[str, Any]:
    payload = data.get("data") if isinstance(data.get("data"), dict) else data
    threads = _extract_comment_threads_from_value(payload, source=source)
    if threads:
        return threads
    return _comment_empty(status="empty", source=source)


def _parse_comment_dom_payload(data: dict[str, Any], *, source: str) -> dict[str, Any]:
    raw_items = data.get("items") if isinstance(data, dict) else []
    if not isinstance(raw_items, list):
        raw_items = []
    items: list[dict[str, Any]] = []
    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            continue
        text = _clean_text(item.get("text") or "")
        if not text:
            continue
        replies = []
        for reply in item.get("replies") or []:
            if not isinstance(reply, dict):
                continue
            reply_text = _clean_text(reply.get("text") or "")
            if not reply_text:
                continue
            replies.append(
                {
                    "id": str(reply.get("id") or ""),
                    "text": reply_text,
                    "author": {"nickname": _clean_text(reply.get("author") or "")},
                    "like_count": _parse_optional_int(reply.get("like_count")) or 0,
                    "time": _clean_text(reply.get("time") or ""),
                    "ip_location": "",
                    "source": source,
                    "replies": [],
                }
            )
        items.append(
            {
                "id": str(item.get("id") or f"browser_dom_{index}"),
                "text": text,
                "author": {"nickname": _clean_text(item.get("author") or "")},
                "like_count": _parse_optional_int(item.get("like_count")) or 0,
                "time": _clean_text(item.get("time") or ""),
                "ip_location": "",
                "source": source,
                "replies": replies,
            }
        )
    total_count = _parse_optional_int(data.get("total_count") if isinstance(data, dict) else None)
    items = _dedupe_comment_records(items)
    reply_count = sum(len(item.get("replies") or []) for item in items)
    loaded_count = _parse_optional_int(data.get("loaded_count") if isinstance(data, dict) else None)
    stable_rounds = _parse_optional_int(data.get("stable_rounds") if isinstance(data, dict) else None)
    loaded_count = loaded_count if loaded_count is not None else len(items) + reply_count
    return {
        "status": "ok" if items else "empty",
        "source": source,
        "count": len(items),
        "total_count": total_count,
        "reply_count": reply_count,
        "loaded_count": loaded_count,
        "reached_end": bool(data.get("reached_end")) if isinstance(data, dict) else False,
        "stable_rounds": stable_rounds,
        "has_more": bool(total_count is not None and total_count > loaded_count),
        "cursor": "",
        "items": items,
    }


def _parse_browser_note_metadata_payload(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    title = _clean_title(data.get("title") or "")
    content = _clean_text(data.get("content") or "")
    author_nickname = _clean_text(data.get("author_nickname") or "")
    comment_count = _parse_count(data.get("comment_count_text"))
    stats = _pick_stats([])
    if comment_count is not None:
        stats = {
            **stats,
            "comment_count": comment_count,
            "source": "browser_cdp:dom",
            "status": "ok",
        }
    return {
        "title": title,
        "content": content,
        "author": {"nickname": author_nickname} if author_nickname else {},
        "stats": stats,
        "source": "browser_cdp:dom",
    }


def _merge_note_metadata_from_browser_dom(note: ParsedNote, metadata: dict[str, Any]) -> None:
    if not metadata:
        return
    title = _clean_title(metadata.get("title") or "")
    content = _clean_text(metadata.get("content") or "")
    if title:
        note.title = title
    if content and (len(content) > len(note.content or "") or note.content in {"3 亿人的生活经验，都在小红书"}):
        note.content = content

    author = metadata.get("author") if isinstance(metadata.get("author"), dict) else {}
    nickname = _clean_text(author.get("nickname") or "")
    if nickname:
        note.author = {**(note.author or {}), "nickname": nickname}

    dom_stats = metadata.get("stats") if isinstance(metadata.get("stats"), dict) else {}
    if dom_stats.get("status") == "ok":
        merged_stats = dict(note.stats or _pick_stats([]))
        for key in ("like_count", "collect_count", "comment_count", "share_count"):
            if dom_stats.get(key) is not None:
                merged_stats[key] = dom_stats[key]
        if any(merged_stats.get(key) is not None for key in ("like_count", "collect_count", "comment_count", "share_count")):
            merged_stats["status"] = "ok"
            merged_stats["source"] = merged_stats.get("source") or "browser_cdp:dom"
        note.stats = merged_stats

    raw_metadata = dict(note.raw_metadata or {})
    browser_dom = dict(raw_metadata.get("browser_dom") or {})
    browser_dom.update(
        {
            "metadata_source": metadata.get("source") or "browser_cdp:dom",
            "title_chars": len(title),
            "content_chars": len(content),
            "author_present": bool(nickname),
        }
    )
    raw_metadata["browser_dom"] = browser_dom
    note.raw_metadata = raw_metadata


def _cdp_http_base_url() -> str:
    raw = (
        os.environ.get("HERMES_XHS_BROWSER_CDP_URL")
        or os.environ.get("BROWSER_CDP_URL")
        or os.environ.get("CHROME_CDP_URL")
        or "http://127.0.0.1:9222"
    ).strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    host = (parsed.hostname or "").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return ""
    scheme = "http" if parsed.scheme in {"ws", "wss"} else parsed.scheme
    if scheme not in {"http", "https"}:
        return ""
    netloc = parsed.netloc
    if not netloc:
        return ""
    return f"{scheme}://{netloc}"


async def _fetch_cdp_targets(cdp_base_url: str) -> list[dict[str, Any]]:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=3.0, follow_redirects=False, trust_env=False) as client:
            response = await client.get(f"{cdp_base_url.rstrip('/')}/json/list")
        if response.status_code >= 400:
            return []
        data = response.json()
    except Exception:
        return []
    return data if isinstance(data, list) else []


async def _browser_cdp_runtime_evaluate(
    ws_url: str,
    expression: str,
    *,
    await_promise: bool = False,
) -> Any | None:
    try:
        import websockets
    except ImportError:
        return None

    try:
        async with websockets.connect(ws_url, max_size=50 * 1024 * 1024) as ws:
            params = {"expression": expression, "returnByValue": True}
            if await_promise:
                params["awaitPromise"] = True
            await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": params}))
            while True:
                message = json.loads(await ws.recv())
                if message.get("id") != 1:
                    continue
                if message.get("error"):
                    return None
                result = ((message.get("result") or {}).get("result") or {}).get("value")
                return result
    except Exception:
        return None


def _comment_dom_extractor_js(max_comments: int) -> str:
    max_comments = max(0, min(int(max_comments or 0), 1000))
    return (
        r"""
(async () => {
  const maxComments = __MAX_COMMENTS__;
  const sleep = (ms) => new Promise((done) => setTimeout(done, ms));
  const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const lines = (el) => clean(el && el.innerText).split(' ').filter(Boolean);
  const numberLike = (value) => {
    const text = clean(value);
    if (!text || text === '赞' || text === '回复') return 0;
    const match = text.match(/(\d+(?:\.\d+)?)(万|k|K)?/);
    if (!match) return 0;
    const base = Number(match[1]);
    if (!Number.isFinite(base)) return 0;
    if (match[2] === '万') return Math.round(base * 10000);
    if (match[2] === 'k' || match[2] === 'K') return Math.round(base * 1000);
    return Math.round(base);
  };
  const parseItem = (item, idPrefix) => {
    const author = clean(item.querySelector('.author')?.innerText).replace(/\s*作者\s*$/, '');
    let text = clean(item.querySelector('.content')?.innerText || item.querySelector('.note-text')?.innerText);
    if (!text) {
      const itemLines = String(item.innerText || '').split('\n').map(clean).filter(Boolean);
      const dateIndex = itemLines.findIndex((line) => /^\d{2}-\d{2}/.test(line));
      let startIndex = author && itemLines[0] === author ? 1 : 0;
      if (itemLines[startIndex] === '作者') startIndex += 1;
      const endIndex = dateIndex > startIndex ? dateIndex : itemLines.length;
      text = clean(itemLines.slice(startIndex, endIndex)
        .filter((line) => !['作者', '赞', '回复'].includes(line))
        .filter((line) => !/^\d+(?:\.\d+)?(?:万|k|K)?$/.test(line))
        .join(' '));
    }
    const time = clean(item.querySelector('.date')?.innerText);
    const infoLines = lines(item.querySelector('.info'));
    const numeric = infoLines.filter((line) => /^\d+(?:\.\d+)?(?:万|k|K)?$/.test(line));
    return {
      id: `${idPrefix}_${Math.abs((author + text + time).split('').reduce((acc, ch) => ((acc << 5) - acc + ch.charCodeAt(0)) | 0, 0))}`,
      author,
      text,
      time,
      like_count: numberLike(numeric[0] || item.querySelector('.like-wrapper .count')?.innerText || ''),
      reply_count: numberLike(numeric[1] || ''),
      replies: []
    };
  };
  const getTotalCount = () => {
    const totalText = clean(document.querySelector('.comments-container .total')?.innerText || '');
    const totalMatch = totalText.match(/(\d+(?:\.\d+)?)(万)?/);
    return totalMatch ? Math.round(Number(totalMatch[1]) * (totalMatch[2] ? 10000 : 1)) : null;
  };
  const collectVisible = () => {
    const parents = Array.from(document.querySelectorAll('.comments-container .parent-comment'));
    return parents.map((parent, index) => {
      const main = parent.querySelector(':scope > .comment-item');
      if (!main) return null;
      const record = parseItem(main, `comment_${index + 1}`);
      record.replies = Array.from(parent.querySelectorAll(':scope > .reply-container .comment-item-sub'))
        .map((reply, replyIndex) => parseItem(reply, `reply_${index + 1}_${replyIndex + 1}`))
        .filter((reply) => reply.text);
      return record;
    }).filter((item) => item && item.text);
  };
  const loadedItemCount = (items) => items.reduce((sum, item) => sum + 1 + ((item.replies || []).length), 0);
  const keyFor = (item) => [item.author, item.text, item.time].join('\u241f');
  const mergeInto = (map) => {
    for (const item of collectVisible()) {
      const key = keyFor(item);
      const old = map.get(key);
      if (!old || (item.replies || []).length > (old.replies || []).length) {
        map.set(key, item);
      }
    }
  };
  const clickExpandReplies = () => {
    let clicked = 0;
    const buttons = Array.from(document.querySelectorAll('.show-more, .reply-container .show-more, div, span, button'));
    for (const button of buttons) {
      const text = clean(button.innerText || button.textContent || '');
      if (!/^展开\s*\d+\s*条回复/.test(text)) continue;
      const rect = button.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) continue;
      button.click();
      clicked += 1;
      if (clicked >= 20) break;
    }
    return clicked;
  };
  const scrollTargets = () => Array.from(new Set([
    document.querySelector('.note-scroller'),
    document.querySelector('.comments-container'),
    document.querySelector('.interaction-container'),
    document.scrollingElement,
    document.documentElement,
    document.body,
  ].filter(Boolean)));
  const scrollStep = () => {
    const last = Array.from(document.querySelectorAll('.comments-container .parent-comment')).at(-1);
    if (last) {
      last.scrollIntoView({block: 'end'});
    }
    for (const target of scrollTargets()) {
      const step = Math.max(700, Math.round((target.clientHeight || window.innerHeight || 900) * 0.9));
      if ('scrollTop' in target) {
        target.scrollTop = Math.min(target.scrollHeight || 0, Math.max((target.scrollTop || 0) + step, target.scrollHeight || 0));
        target.dispatchEvent(new WheelEvent('wheel', {deltaY: step, bubbles: true}));
        target.dispatchEvent(new Event('scroll', {bubbles: true}));
      }
    }
    window.dispatchEvent(new WheelEvent('wheel', {deltaY: Math.max(700, Math.round(window.innerHeight * 0.9)), bubbles: true}));
  };

  const collected = new Map();
  let lastCount = -1;
  let stableRounds = 0;
  let reachedEnd = false;
  const maxRounds = 100;
  for (let round = 0; round < maxRounds; round += 1) {
    mergeInto(collected);
    clickExpandReplies();
    await sleep(250);
    mergeInto(collected);

    const total_count = getTotalCount();
    const itemsNow = Array.from(collected.values());
    const loadedTotal = loadedItemCount(itemsNow);
    const targetCount = total_count || maxComments;
    if (loadedTotal >= targetCount) {
      reachedEnd = true;
      break;
    }
    const pageText = clean(document.querySelector('.comments-container')?.innerText || document.body?.innerText || '');
    if (/没有更多|到底了|已经到底|暂时没有更多/.test(pageText) && collected.size > 0) {
      reachedEnd = true;
      break;
    }
    if (loadedTotal === lastCount) {
      stableRounds += 1;
    } else {
      stableRounds = 0;
      lastCount = loadedTotal;
    }
    if (stableRounds >= 18) break;
    scrollStep();
    await sleep(650);
  }
  mergeInto(collected);
  const items = Array.from(collected.values()).slice(0, maxComments);
  return {total_count: getTotalCount(), loaded_count: loadedItemCount(items), reached_end: reachedEnd, stable_rounds: stableRounds, items};
})()
"""
    ).replace("__MAX_COMMENTS__", str(max_comments))


_NOTE_DOM_METADATA_JS = r"""
(() => {
  const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const pick = (selectors) => {
    for (const selector of selectors) {
      const text = clean(document.querySelector(selector)?.innerText || document.querySelector(selector)?.textContent || '');
      if (text) return text;
    }
    return '';
  };
  const title = pick([
    '.note-content .title',
    '.note-scroller .title',
    '.interaction-container .title',
    '.title'
  ]);
  const content = pick([
    '.note-content .desc .note-text',
    '.note-content .desc',
    '.note-scroller .desc .note-text',
    '.note-scroller .desc',
    '.desc .note-text',
    '.desc'
  ]);
  const author_nickname = pick([
    '.author-container .name',
    '.author-wrapper .author',
    '.user-nickname',
    '.username',
    '.name'
  ]).replace(/\s*作者\s*$/, '');
  const comment_count_text = pick([
    '.comments-container .total',
    '.comments-el .total'
  ]);
  return {title, content, author_nickname, comment_count_text};
})()
"""


async def _fetch_note_metadata_from_browser_cdp(note: ParsedNote) -> tuple[dict[str, Any], list[str]]:
    cdp_base_url = _cdp_http_base_url()
    if not cdp_base_url:
        return ({}, [])

    targets = await _fetch_cdp_targets(cdp_base_url)
    note_id = (note.note_id or "").lower()
    pages = [
        item
        for item in targets
        if item.get("type") == "page" and item.get("webSocketDebuggerUrl")
    ]
    target = next((item for item in pages if note_id and note_id in str(item.get("url", "")).lower()), None)
    if target is None:
        return ({}, [])

    payload = await _browser_cdp_runtime_evaluate(str(target.get("webSocketDebuggerUrl")), _NOTE_DOM_METADATA_JS)
    metadata = _parse_browser_note_metadata_payload(payload if isinstance(payload, dict) else {})
    if metadata.get("title") or metadata.get("content"):
        return (metadata, ["Note title/body metadata was extracted from the logged-in Browser/CDP page DOM."])
    return ({}, [])


async def _fetch_comment_threads_from_browser_cdp(
    note: ParsedNote,
    *,
    max_comments: int,
) -> tuple[dict[str, Any], list[str]]:
    max_comments = max(0, min(int(max_comments or 0), 1000))
    if max_comments <= 0:
        return (_comment_empty(status="skipped_limit", source="browser_cdp:dom"), [])

    cdp_base_url = _cdp_http_base_url()
    if not cdp_base_url:
        return (_comment_empty(status="cdp_unavailable", source="browser_cdp:dom"), [])

    targets = await _fetch_cdp_targets(cdp_base_url)
    note_id = (note.note_id or "").lower()
    pages = [
        item
        for item in targets
        if item.get("type") == "page" and item.get("webSocketDebuggerUrl")
    ]
    target = next((item for item in pages if note_id and note_id in str(item.get("url", "")).lower()), None)
    if target is None:
        return (_comment_empty(status="cdp_no_xhs_page", source="browser_cdp:dom"), [])

    payload = await _browser_cdp_runtime_evaluate(
        str(target.get("webSocketDebuggerUrl")),
        _comment_dom_extractor_js(max_comments),
        await_promise=True,
    )
    threads = _parse_comment_dom_payload(payload if isinstance(payload, dict) else {}, source="browser_cdp:dom")
    threads = _limit_comment_threads(threads, max_comments)
    if threads.get("items"):
        total_count = threads.get("total_count")
        count = threads.get("count") or 0
        loaded_count = threads.get("loaded_count") or count
        warnings = [
            f"Comment bodies were extracted from the logged-in Browser/CDP page DOM ({loaded_count}/{total_count or 'unknown'} visible comments/replies, {count} top-level threads)."
        ]
        if total_count is not None and loaded_count < total_count:
            warnings.append(
                f"Browser/CDP comment extraction stopped before the displayed total ({loaded_count}/{total_count}); the page may have stopped lazy-loading or max_comments may be too low."
            )
        return (threads, warnings)
    return (threads, [])


async def _fetch_comment_threads_from_api(
    note: ParsedNote,
    *,
    max_comments: int,
    cookie: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    max_comments = max(0, min(int(max_comments or 0), 1000))
    if max_comments <= 0:
        return (_comment_empty(status="skipped_limit", source="api:sns/web/v2/comment/page"), [])

    cookie = (cookie or _xhs_cookie_from_env()).strip()
    if not cookie:
        return (
            {
                **_comment_empty(status="requires_login", source="api:sns/web/v2/comment/page"),
                "auth_required": True,
            },
            [
                "Comment bodies are loaded through Xiaohongshu's authenticated comment API; set HERMES_XHS_COOKIE or HERMES_XHS_COOKIE_FILE to fetch them locally."
            ],
        )

    from tools.url_safety import is_safe_url
    import httpx

    referer = note.resolved_url or note.source_url or "https://www.xiaohongshu.com/"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": referer,
        "Origin": "https://www.xiaohongshu.com",
        "Cookie": cookie,
    }
    warnings: list[str] = []
    all_items: list[dict[str, Any]] = []
    cursor = ""
    has_more = None
    status = "empty"
    source = "api:sns/web/v2/comment/page"
    async with httpx.AsyncClient(
        timeout=20.0,
        follow_redirects=True,
        event_hooks={"response": [_ssrf_redirect_guard]},
    ) as client:
        for _page_index in range(max(1, min(5, (max_comments + 19) // 20 if max_comments else 1))):
            url = _comment_api_url(note, cursor=cursor)
            if not is_safe_url(url):
                raise ValueError("Blocked unsafe Xiaohongshu comment API URL")
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            try:
                data = response.json()
            except json.JSONDecodeError:
                return (
                    {
                        **_comment_empty(status="api_failed", source=source),
                        "http_status": response.status_code,
                    },
                    ["Comment API returned a non-JSON response."],
                )
            if data.get("success") is False:
                code = data.get("code")
                message = _clean_text(data.get("msg") or data.get("message") or "")
                if code == -101:
                    return (
                        {
                            **_comment_empty(status="requires_login", source=source),
                            "auth_required": True,
                            "api_code": code,
                            "api_message": message,
                        },
                        [
                            "Comment API requires a valid Xiaohongshu web login cookie; set HERMES_XHS_COOKIE or HERMES_XHS_COOKIE_FILE and restart Hermes."
                        ],
                    )
                return (
                    {
                        **_comment_empty(status="api_failed", source=source),
                        "api_code": code,
                        "api_message": message,
                    },
                    [f"Comment API failed: {message or code or 'unknown error'}"],
                )
            threads = _parse_comment_api_payload(data, source=source)
            status = threads.get("status") or status
            has_more = threads.get("has_more")
            cursor = threads.get("cursor") or ""
            all_items.extend(threads.get("items") or [])
            all_items = _dedupe_comment_records(all_items)
            if len(all_items) >= max_comments or not has_more or not cursor:
                break
    all_items = all_items[:max_comments]
    return (
        {
            "status": "ok" if all_items else status,
            "source": source,
            "count": len(all_items),
            "has_more": has_more,
            "cursor": cursor,
            "items": all_items,
        },
        warnings,
    )


def _find_balanced_json_object(text: str, marker: str) -> str | None:
    marker_pos = text.find(marker)
    if marker_pos < 0:
        return None
    start = text.find("{", marker_pos)
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _json_loads_lenient(raw: str) -> Any | None:
    if not raw:
        return None
    cleaned = html.unescape(raw.strip())
    cleaned = cleaned.replace("</script>", "")
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        cleaned = re.sub(r":\s*undefined\b", ": null", cleaned)
        cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return None


def _iter_json_blobs(html_text: str) -> list[Any]:
    blobs: list[Any] = []
    for raw in _JSON_LD_RE.findall(html_text or ""):
        parsed = _json_loads_lenient(raw)
        if parsed is not None:
            blobs.append(parsed)
    for script in _SCRIPT_RE.findall(html_text or ""):
        for marker in _INITIAL_STATE_MARKERS:
            raw = _find_balanced_json_object(script, marker)
            parsed = _json_loads_lenient(raw or "")
            if parsed is not None:
                blobs.append(parsed)
                break
    return blobs


def _walk_json(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _collect_from_json(blobs: list[Any], base_url: str) -> dict[str, Any]:
    titles: list[str] = []
    contents: list[str] = []
    images: list[str] = []
    videos: list[ParsedVideo] = []
    subtitles: list[ParsedSubtitle] = []
    stats_candidates: list[dict[str, Any]] = []
    comment_candidates: list[dict[str, Any]] = []
    author: dict[str, str] = {}

    title_keys = {"title", "displaytitle", "headline", "name"}
    content_keys = {"desc", "description", "content", "notetext", "text", "articlebody"}
    image_keys = {
        "image",
        "images",
        "imagelist",
        "imageurl",
        "url",
        "urldefault",
        "urlpre",
        "originalurl",
        "thumbnail",
        "cover",
        "src",
    }
    video_markers = {
        "video",
        "videoinfo",
        "videourl",
        "media",
        "stream",
        "masterurl",
        "backupurls",
        "h264",
        "h265",
        "dash",
        "playurl",
    }
    subtitle_markers = {"subtitle", "subtitles", "caption", "captions", "srt", "vtt"}

    for blob in blobs:
        for node in _walk_json(blob):
            direct_stats = _extract_stats_from_node(node, source="json")
            if direct_stats and _stats_score(direct_stats)[0] >= 2:
                stats_candidates.append(direct_stats)
            for key, item in node.items():
                key_l = str(key).lower()
                if "interact" in key_l and isinstance(item, dict):
                    interact_stats = _extract_stats_from_node(item, source=f"json:{key}")
                    if interact_stats:
                        stats_candidates.append(interact_stats)
                if "comment" in key_l and isinstance(item, (dict, list)):
                    comment_threads = _extract_comment_threads_from_value(item, source=f"json:{key}")
                    if comment_threads:
                        comment_candidates.append(comment_threads)
                if isinstance(item, str):
                    cleaned = _clean_text(item)
                    if key_l in title_keys and 2 <= len(cleaned) <= 160:
                        titles.append(cleaned)
                    elif key_l in content_keys and len(cleaned) >= 6:
                        contents.append(cleaned)
                    if key_l in image_keys or "url" in key_l or "image" in key_l:
                        normalised = _normalise_image_url(item, base_url=base_url)
                        if normalised:
                            images.append(normalised)
                    if (
                        key_l in subtitle_markers
                        or "subtitle" in key_l
                        or "caption" in key_l
                        or "srt" in key_l
                    ):
                        subtitle_url = _normalise_subtitle_url(item, base_url=base_url)
                        if subtitle_url:
                            subtitles.append(ParsedSubtitle(source_url=subtitle_url, format=_subtitle_format_from_url(subtitle_url)))
                    if (
                        key_l in video_markers
                        or "video" in key_l
                        or "stream" in key_l
                        or "master" in key_l
                        or "backup" in key_l
                        or "h264" in key_l
                        or "h265" in key_l
                        or "url" in key_l
                    ):
                        video_url = _normalise_video_url(item, base_url=base_url)
                        if video_url:
                            videos.append(_build_video_candidate(video_url, node, base_url=base_url))
                elif key_l in image_keys:
                    for child in _walk_json(item):
                        for child_value in child.values():
                            if isinstance(child_value, str):
                                normalised = _normalise_image_url(child_value, base_url=base_url)
                                if normalised:
                                    images.append(normalised)
                                subtitle_url = _normalise_subtitle_url(child_value, base_url=base_url)
                                if subtitle_url:
                                    subtitles.append(ParsedSubtitle(source_url=subtitle_url, format=_subtitle_format_from_url(subtitle_url)))
                elif (
                    key_l in video_markers
                    or "video" in key_l
                    or "stream" in key_l
                    or "media" in key_l
                    or "h264" in key_l
                    or "h265" in key_l
                ):
                    for child in _walk_json(item):
                        for child_value in child.values():
                            if isinstance(child_value, str):
                                video_url = _normalise_video_url(child_value, base_url=base_url)
                                if video_url:
                                    videos.append(_build_video_candidate(video_url, child, base_url=base_url))
                                subtitle_url = _normalise_subtitle_url(child_value, base_url=base_url)
                                if subtitle_url:
                                    subtitles.append(ParsedSubtitle(source_url=subtitle_url, format=_subtitle_format_from_url(subtitle_url)))
                elif key_l in subtitle_markers or "subtitle" in key_l or "caption" in key_l:
                    for child in _walk_json(item):
                        for child_value in child.values():
                            if isinstance(child_value, str):
                                subtitle_url = _normalise_subtitle_url(child_value, base_url=base_url)
                                if subtitle_url:
                                    subtitles.append(ParsedSubtitle(source_url=subtitle_url, format=_subtitle_format_from_url(subtitle_url)))
                if key_l in {"nickname", "nick", "username"} and isinstance(item, str):
                    author.setdefault("nickname", _clean_text(item))
                if key_l in {"userid", "user_id", "authorid"} and isinstance(item, str):
                    author.setdefault("id", _clean_text(item))

    return {
        "title": _clean_title(titles[0]) if titles else "",
        "content": max((_clean_text(item) for item in contents), key=len, default=""),
        "images": _dedupe(images),
        "videos": _dedupe_videos(videos),
        "subtitles": _dedupe_subtitles(subtitles),
        "stats": _pick_stats(stats_candidates),
        "comment_threads": _pick_comment_threads(comment_candidates),
        "author": author,
    }


def _extract_metadata_from_html(html_text: str, base_url: str) -> dict[str, Any]:
    meta = _meta_tags(html_text)
    blobs = _iter_json_blobs(html_text)
    json_data = _collect_from_json(blobs, base_url=base_url)

    meta_images = []
    for key in ("og:image", "twitter:image", "image"):
        normalised = _normalise_image_url(meta.get(key, ""), base_url=base_url)
        if normalised:
            meta_images.append(normalised)

    meta_videos: list[ParsedVideo] = []
    for key in ("og:video", "og:video:url", "og:video:secure_url", "twitter:player", "video"):
        normalised = _normalise_video_url(meta.get(key, ""), base_url=base_url)
        if normalised:
            meta_videos.append(
                ParsedVideo(
                    source_url=normalised,
                    cover_url=meta_images[0] if meta_images else "",
                    duration_ms=_extract_duration_ms(
                        {"duration": meta.get("video:duration") or meta.get("og:video:duration")}
                    ),
                    width=_parse_optional_int(meta.get("og:video:width")),
                    height=_parse_optional_int(meta.get("og:video:height")),
                    format=_video_format_from_url(normalised),
                )
            )

    regex_images = []
    regex_videos: list[ParsedVideo] = []
    regex_subtitles: list[ParsedSubtitle] = []
    normalised_html = (html_text or "").replace("\\/", "/").replace("\\u002F", "/")
    for raw in _IMG_URL_RE.findall(normalised_html):
        normalised = _normalise_image_url(raw, base_url=base_url)
        if normalised:
            regex_images.append(normalised)
        video_url = _normalise_video_url(raw, base_url=base_url)
        if video_url:
            regex_videos.append(ParsedVideo(source_url=video_url, format=_video_format_from_url(video_url)))
        subtitle_url = _normalise_subtitle_url(raw, base_url=base_url)
        if subtitle_url:
            regex_subtitles.append(
                ParsedSubtitle(source_url=subtitle_url, format=_subtitle_format_from_url(subtitle_url))
            )

    title = (
        _clean_title(meta.get("og:title", ""))
        or _clean_title(meta.get("twitter:title", ""))
        or json_data.get("title", "")
        or _clean_title(_html_title(html_text))
    )
    content = (
        _clean_text(meta.get("og:description", ""))
        or _clean_text(meta.get("description", ""))
        or json_data.get("content", "")
    )
    videos = _dedupe_videos(meta_videos + json_data.get("videos", []) + regex_videos)
    subtitles = _dedupe_subtitles(json_data.get("subtitles", []) + regex_subtitles)
    stats = json_data.get("stats") or _pick_stats([])
    if stats.get("status") != "ok":
        regex_stats = _extract_stats_from_text(html_text)
        if regex_stats:
            stats = regex_stats
    video_cover_urls = [video.cover_url for video in videos if video.cover_url]
    meta_type = (meta.get("og:type") or meta.get("type") or meta.get("twitter:card") or "").lower()
    note_type_hint = "video" if "video" in meta_type else ""

    return {
        "title": title,
        "content": content,
        "author": json_data.get("author") or {},
        "images": _dedupe(meta_images + video_cover_urls + json_data.get("images", []) + regex_images),
        "videos": videos,
        "subtitles": subtitles,
        "stats": stats,
        "comment_threads": json_data.get("comment_threads") or _comment_empty(),
        "note_type_hint": note_type_hint,
        "meta": meta,
        "json_blob_count": len(blobs),
    }


async def _ssrf_redirect_guard(response):
    if not response.next_request:
        return
    redirect_url = str(response.next_request.url)
    from tools.url_safety import is_safe_url

    if not is_safe_url(redirect_url):
        raise ValueError(f"Blocked unsafe redirect while resolving Xiaohongshu URL")


async def _fetch_page(url: str) -> PageFetch:
    from tools.url_safety import is_safe_url

    if not is_safe_url(url):
        raise ValueError("Blocked unsafe Xiaohongshu URL")
    if not _is_allowed_xhs_url(url):
        raise ValueError("Only Xiaohongshu/xhslink URLs are supported")

    import httpx

    warnings: list[str] = []
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    async with httpx.AsyncClient(
        timeout=25.0,
        follow_redirects=True,
        event_hooks={"response": [_ssrf_redirect_guard]},
    ) as client:
        response = await client.get(url, headers=headers)
    if response.status_code >= 400:
        warnings.append(f"Page request returned HTTP {response.status_code}; extraction may be partial.")
    return PageFetch(
        original_url=url,
        final_url=str(response.url),
        html_text=response.text or "",
        status_code=response.status_code,
        warnings=warnings,
    )


def _parse_note(fetch: PageFetch) -> ParsedNote:
    note_id = _extract_note_id_from_url(fetch.final_url) or _extract_note_id_from_url(fetch.original_url)
    if not note_id:
        digest = hashlib.sha256(fetch.final_url.encode("utf-8")).hexdigest()[:24]
        note_id = f"unknown_{digest}"
        fetch.warnings.append("Could not find a 24-character note_id in the URL; using a stable hash id.")

    metadata = _extract_metadata_from_html(fetch.html_text, base_url=fetch.final_url)
    warnings = list(fetch.warnings)
    if not metadata.get("title"):
        warnings.append("Could not extract a title from the page metadata.")
    if not metadata.get("content"):
        warnings.append("Could not extract note body text; Xiaohongshu may require login for full content.")
    videos = metadata.get("videos", [])
    if videos:
        note_type = "video"
    elif metadata.get("note_type_hint") == "video":
        note_type = "video"
        warnings.append("Page metadata indicates a video note, but no video URL was extracted.")
    elif metadata.get("images"):
        note_type = "image_text"
    else:
        note_type = "unknown"

    if not metadata.get("images") and note_type != "video":
        warnings.append("Could not extract note image URLs from the page metadata.")
    if note_type == "video" and not metadata.get("images"):
        warnings.append("Could not extract a video cover image from the page metadata.")

    return ParsedNote(
        note_id=note_id,
        source_url=fetch.original_url,
        resolved_url=fetch.final_url,
        title=metadata.get("title", ""),
        content=metadata.get("content", ""),
        note_type=note_type,
        author=metadata.get("author", {}),
        stats=metadata.get("stats", {}),
        comment_threads=metadata.get("comment_threads", {}),
        image_urls=metadata.get("images", []),
        videos=videos,
        subtitles=metadata.get("subtitles", []),
        raw_metadata={
            "status_code": fetch.status_code,
            "meta": metadata.get("meta", {}),
            "json_blob_count": metadata.get("json_blob_count", 0),
            "note_type_hint": metadata.get("note_type_hint", ""),
            "subtitle_count": len(metadata.get("subtitles", [])),
        },
        warnings=warnings,
    )


def _looks_like_image(data: bytes) -> bool:
    if len(data) < 4:
        return False
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if data[:3] == b"\xff\xd8\xff":
        return True
    if data[:6] in {b"GIF87a", b"GIF89a"}:
        return True
    if data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP":
        return True
    return False


async def _download_image(url: str, destination: Path, referer: str) -> None:
    from tools.url_safety import is_safe_url

    if not is_safe_url(url):
        raise ValueError("Blocked unsafe image URL")

    import httpx

    async with httpx.AsyncClient(
        timeout=30.0,
        follow_redirects=True,
        event_hooks={"response": [_ssrf_redirect_guard]},
    ) as client:
        response = await client.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; HermesAgent/1.0)",
                "Accept": "image/*,*/*;q=0.8",
                "Referer": referer or "https://www.xiaohongshu.com/",
            },
        )
    response.raise_for_status()
    if not _looks_like_image(response.content):
        raise ValueError("Downloaded data is not an image")
    destination.write_bytes(response.content)


async def _download_note_images(note: ParsedNote, note_dir: Path, max_images: int) -> tuple[list[dict[str, Any]], list[str]]:
    images_dir = note_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    image_records: list[dict[str, Any]] = []
    warnings: list[str] = []
    cover_urls = {video.cover_url for video in note.videos if video.cover_url}
    for index, url in enumerate(note.image_urls[:max_images], start=1):
        try:
            ext = ".jpg"
            parsed_path = urlparse(url).path.lower()
            for candidate in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
                if parsed_path.endswith(candidate):
                    ext = ".jpg" if candidate == ".jpeg" else candidate
                    break
            path = images_dir / f"{index:02d}{ext}"
            await _download_image(url, path, referer=note.resolved_url)
            image_records.append(
                {
                    "index": index,
                    "role": "cover" if url in cover_urls else "gallery",
                    "source_url": url,
                    "local_path": str(path),
                    "ocr_text": "",
                    "ocr_status": "pending",
                }
            )
        except Exception as exc:
            warnings.append(f"Image {index} download failed: {exc}")
    return image_records, warnings


def _looks_like_video(data: bytes) -> bool:
    if len(data) < 8:
        return False
    if b"ftyp" in data[:32]:
        return True
    if data[:4] == b"\x1aE\xdf\xa3":
        return True
    if data[:7] == b"#EXTM3U":
        return True
    return False


def _video_extension_for_url(url: str) -> str:
    path = urlparse(url).path.lower()
    for ext in _VIDEO_EXTENSIONS:
        if path.endswith(ext):
            return ext
    return ".mp4"


async def _download_video(url: str, destination: Path, referer: str, max_bytes: int) -> dict[str, Any]:
    from tools.url_safety import is_safe_url

    if not is_safe_url(url):
        raise ValueError("Blocked unsafe video URL")
    if _video_extension_for_url(url) == ".m3u8":
        raise ValueError("m3u8 playlist download is not supported in P0; storing URL only.")

    import httpx

    destination.parent.mkdir(parents=True, exist_ok=True)
    bytes_written = 0
    content_type = ""
    first_chunk = b""
    async with httpx.AsyncClient(
        timeout=60.0,
        follow_redirects=True,
        event_hooks={"response": [_ssrf_redirect_guard]},
    ) as client:
        async with client.stream(
            "GET",
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; HermesAgent/1.0)",
                "Accept": "video/*,*/*;q=0.8",
                "Referer": referer or "https://www.xiaohongshu.com/",
            },
        ) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            content_length = _parse_optional_int(response.headers.get("content-length"))
            if content_length is not None and content_length > max_bytes:
                raise ValueError(f"Video is larger than max_video_mb ({content_length} > {max_bytes} bytes).")
            with destination.open("wb") as handle:
                async for chunk in response.aiter_bytes():
                    if not chunk:
                        continue
                    bytes_written += len(chunk)
                    if bytes_written > max_bytes:
                        handle.close()
                        destination.unlink(missing_ok=True)
                        raise ValueError(f"Video exceeded max_video_mb ({bytes_written} > {max_bytes} bytes).")
                    if len(first_chunk) < 64:
                        first_chunk += chunk[: 64 - len(first_chunk)]
                    handle.write(chunk)
    if not bytes_written:
        destination.unlink(missing_ok=True)
        raise ValueError("Downloaded video was empty")
    content_type_l = content_type.lower()
    suffix_l = destination.suffix.lower()
    suffix_ok_with_generic_type = suffix_l in {".mp4", ".m4v", ".mov", ".webm"} and content_type_l in {
        "",
        "application/octet-stream",
        "binary/octet-stream",
    }
    if not (
        content_type_l.startswith("video/")
        or _looks_like_video(first_chunk)
        or suffix_ok_with_generic_type
    ):
        destination.unlink(missing_ok=True)
        raise ValueError("Downloaded data is not a recognized video")
    return {
        "local_path": str(destination),
        "download_status": "ok",
        "bytes": bytes_written,
        "content_type": content_type,
    }


async def _download_note_videos(
    note: ParsedNote,
    note_dir: Path,
    *,
    download_video: bool,
    max_video_mb: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    videos_dir = note_dir / "videos"
    videos_dir.mkdir(parents=True, exist_ok=True)
    video_records: list[dict[str, Any]] = []
    warnings: list[str] = []
    max_bytes = max(1, max_video_mb) * 1024 * 1024
    for index, video in enumerate(note.videos, start=1):
        record = video.to_record(index)
        record.update(
            {
                "local_path": "",
                "download_status": "pending" if download_video else "skipped_disabled",
                "bytes": 0,
                "content_type": "",
            }
        )
        if not download_video:
            video_records.append(record)
            continue
        ext = _video_extension_for_url(video.source_url)
        if ext == ".m3u8":
            record["download_status"] = "skipped_playlist"
            warnings.append(f"Video {index} download skipped: m3u8 playlist download is not supported in P0; storing URL only.")
            video_records.append(record)
            continue
        try:
            path = videos_dir / f"{index:02d}{ext}"
            download = await _download_video(video.source_url, path, note.resolved_url, max_bytes=max_bytes)
            record.update(download)
        except Exception as exc:
            record["download_status"] = "failed"
            warnings.append(f"Video {index} download failed: {exc}")
        video_records.append(record)
    return video_records, warnings


def _parse_srt_timestamp_ms(value: str) -> int | None:
    match = re.match(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})", value.strip())
    if not match:
        return None
    hours, minutes, seconds, millis = (int(part) for part in match.groups())
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def _cjk_char_count(text: str) -> int:
    return sum(1 for char in text if "\u4e00" <= char <= "\u9fff")


def _detect_subtitle_language(text: str) -> str:
    if _cjk_char_count(text) >= 5:
        return "zh"
    latin_chars = sum(1 for char in text if char.isascii() and char.isalpha())
    return "en" if latin_chars >= 10 else "unknown"


def _parse_srt_text(raw_text: str) -> tuple[str, list[dict[str, Any]], int | None]:
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return "", [], None
    blocks = re.split(r"\n\s*\n", text)
    segments: list[dict[str, Any]] = []
    for block in blocks:
        lines = [line.strip("\ufeff ") for line in block.split("\n") if line.strip()]
        if len(lines) < 2:
            continue
        index = _parse_optional_int(lines[0])
        timing_line_index = 1 if index is not None else 0
        if timing_line_index >= len(lines) or "-->" not in lines[timing_line_index]:
            continue
        start_raw, end_raw = [part.strip().split()[0] for part in lines[timing_line_index].split("-->", 1)]
        start_ms = _parse_srt_timestamp_ms(start_raw)
        end_ms = _parse_srt_timestamp_ms(end_raw)
        segment_text = " ".join(lines[timing_line_index + 1 :]).strip()
        if not segment_text:
            continue
        segments.append(
            {
                "index": index or len(segments) + 1,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "text": segment_text,
            }
        )
    transcript_text = "\n".join(segment["text"] for segment in segments).strip()
    end_ms = max((segment.get("end_ms") or 0 for segment in segments), default=0) or None
    return transcript_text, segments, end_ms


async def _download_subtitle(url: str, destination: Path, referer: str) -> str:
    from tools.url_safety import is_safe_url

    if not is_safe_url(url):
        raise ValueError("Blocked unsafe subtitle URL")

    import httpx

    destination.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(
        timeout=30.0,
        follow_redirects=True,
        event_hooks={"response": [_ssrf_redirect_guard]},
    ) as client:
        response = await client.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; HermesAgent/1.0)",
                "Accept": "text/plain,text/vtt,*/*;q=0.8",
                "Referer": referer or "https://www.xiaohongshu.com/",
            },
        )
    response.raise_for_status()
    text = response.text.strip()
    if "-->" not in text:
        raise ValueError("Downloaded subtitle is not SRT/VTT-like text")
    destination.write_text(text, encoding="utf-8")
    return text


async def _download_note_subtitles(note: ParsedNote, note_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    subtitles_dir = note_dir / "subtitles"
    subtitles_dir.mkdir(parents=True, exist_ok=True)
    subtitle_records: list[dict[str, Any]] = []
    warnings: list[str] = []
    for index, subtitle in enumerate(note.subtitles, start=1):
        ext = "." + (subtitle.format or "srt")
        record = subtitle.to_record(index)
        record.update(
            {
                "local_path": "",
                "download_status": "pending",
                "language": "",
                "text": "",
                "char_count": 0,
                "segments": [],
                "end_ms": None,
            }
        )
        try:
            path = subtitles_dir / f"{index:02d}{ext}"
            raw_text = await _download_subtitle(subtitle.source_url, path, note.resolved_url)
            transcript_text, segments, end_ms = _parse_srt_text(raw_text)
            record.update(
                {
                    "local_path": str(path),
                    "download_status": "ok",
                    "language": _detect_subtitle_language(transcript_text),
                    "text": transcript_text,
                    "char_count": len(transcript_text),
                    "segments": segments,
                    "end_ms": end_ms,
                }
            )
        except Exception as exc:
            record["download_status"] = "failed"
            warnings.append(f"Subtitle {index} download failed: {exc}")
        subtitle_records.append(record)
    return subtitle_records, warnings


def _transcript_from_subtitles(
    subtitle_records: list[dict[str, Any]],
    preferred_language: str = _DEFAULT_XHS_STT_LANGUAGE,
) -> dict[str, Any] | None:
    candidates = [
        record
        for record in subtitle_records
        if record.get("download_status") == "ok" and record.get("text")
    ]
    if not candidates:
        return None

    preferred_language = (preferred_language or "").lower()
    candidates.sort(
        key=lambda record: (
            1 if preferred_language and record.get("language") == preferred_language else 0,
            _cjk_char_count(record.get("text", "")),
            len(record.get("text", "")),
        ),
        reverse=True,
    )
    selected = candidates[0]
    return {
        "status": "ok",
        "provider": "xiaohongshu_subtitle",
        "model": "",
        "language": selected.get("language") or preferred_language or "",
        "source": f"subtitle:{selected.get('index')}",
        "text": selected.get("text", ""),
        "segments": selected.get("segments") or [],
        "end_ms": selected.get("end_ms"),
    }


def _annotate_transcript_coverage(transcript: dict[str, Any], video_records: list[dict[str, Any]]) -> None:
    end_ms = transcript.get("end_ms")
    if not end_ms:
        return
    duration_ms = next(
        (
            record.get("duration_ms")
            for record in video_records
            if record.get("duration_ms") and record.get("download_status") == "ok"
        ),
        None,
    ) or next((record.get("duration_ms") for record in video_records if record.get("duration_ms")), None)
    if not duration_ms:
        return
    delta_ms = int(duration_ms) - int(end_ms)
    ratio = max(0.0, min(float(end_ms) / float(duration_ms), 1.0))
    transcript["video_duration_ms"] = int(duration_ms)
    transcript["coverage_ratio"] = round(ratio, 4)
    transcript["coverage_delta_ms"] = delta_ms
    transcript["coverage_status"] = "complete" if ratio >= 0.98 or abs(delta_ms) <= 2000 else "partial"


async def _extract_audio_from_video(video_path: str, audio_path: Path) -> dict[str, Any]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {
            "source": video_path,
            "local_path": "",
            "extract_status": "skipped_no_ffmpeg",
            "error": "ffmpeg not found",
        }
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        ffmpeg,
        "-y",
        "-i",
        video_path,
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        str(audio_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return {
            "source": video_path,
            "local_path": "",
            "extract_status": "failed",
            "error": "ffmpeg audio extraction timed out",
        }
    if proc.returncode != 0:
        detail = (stderr or stdout).decode("utf-8", errors="replace").strip()
        return {
            "source": video_path,
            "local_path": "",
            "extract_status": "failed",
            "error": detail or f"ffmpeg exited with code {proc.returncode}",
        }
    return {
        "source": video_path,
        "local_path": str(audio_path),
        "extract_status": "ok",
    }


async def _transcribe_video_audio(
    video_records: list[dict[str, Any]],
    note_dir: Path,
    *,
    transcribe: bool,
    stt_model: str | None,
    stt_language: str | None = _DEFAULT_XHS_STT_LANGUAGE,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    audio_record: dict[str, Any] = {
        "source": "",
        "local_path": "",
        "extract_status": "skipped_disabled" if not transcribe else "skipped_no_video_file",
    }
    transcript: dict[str, Any] = {
        "status": "skipped_disabled" if not transcribe else "skipped_no_video_file",
        "provider": "",
        "model": stt_model or "",
        "language": stt_language or "",
        "source": "",
        "text": "",
        "segments": [],
        "end_ms": None,
    }
    warnings: list[str] = []
    if not transcribe:
        return audio_record, transcript, warnings

    video_record = next(
        (item for item in video_records if item.get("download_status") == "ok" and item.get("local_path")),
        None,
    )
    if not video_record:
        warnings.append("Video transcription skipped: no downloaded video file is available.")
        return audio_record, transcript, warnings

    audio_path = note_dir / "audio" / f"{int(video_record.get('index') or 1):02d}.wav"
    audio_record = await _extract_audio_from_video(str(video_record["local_path"]), audio_path)
    audio_record["source"] = f"video:{video_record.get('index', 1)}"
    if audio_record.get("extract_status") != "ok":
        transcript["status"] = "skipped_audio_extract_failed"
        warnings.append(f"Video transcription skipped: audio extraction failed ({audio_record.get('error')}).")
        return audio_record, transcript, warnings

    try:
        result = await asyncio.to_thread(
            _transcribe_audio_with_language,
            audio_record["local_path"],
            stt_model,
            stt_language,
        )
    except Exception as exc:
        transcript["status"] = "failed"
        warnings.append(f"Video transcription failed: {exc}")
        return audio_record, transcript, warnings

    if result.get("success"):
        transcript.update(
            {
                "status": "ok",
                "provider": result.get("provider", ""),
                "model": stt_model or "",
                "language": stt_language or "",
                "source": audio_record.get("source", ""),
                "text": str(result.get("transcript") or "").strip(),
                "segments": result.get("segments") or [],
            }
        )
    else:
        transcript["status"] = "failed"
        warnings.append(f"Video transcription failed: {result.get('error') or 'unknown STT error'}")
    return audio_record, transcript, warnings


def _transcribe_audio_with_language(
    audio_path: str,
    stt_model: str | None,
    stt_language: str | None,
) -> dict[str, Any]:
    from tools.transcription_tools import transcribe_audio

    if not stt_language:
        return transcribe_audio(audio_path, stt_model)

    old_value = os.environ.get("HERMES_LOCAL_STT_LANGUAGE")
    os.environ["HERMES_LOCAL_STT_LANGUAGE"] = stt_language
    try:
        return transcribe_audio(audio_path, stt_model)
    finally:
        if old_value is None:
            os.environ.pop("HERMES_LOCAL_STT_LANGUAGE", None)
        else:
            os.environ["HERMES_LOCAL_STT_LANGUAGE"] = old_value


async def _ocr_images(
    image_records: list[dict[str, Any]],
    vision_model: str | None = None,
) -> list[str]:
    warnings: list[str] = []
    if not image_records:
        return warnings
    try:
        from tools.vision_tools import check_vision_requirements, vision_analyze_tool

        if not check_vision_requirements():
            for record in image_records:
                record["ocr_status"] = "skipped_no_vision_model"
            warnings.append("OCR skipped: Hermes vision model is not configured.")
            return warnings
    except Exception as exc:
        for record in image_records:
            record["ocr_status"] = "skipped_no_vision_tool"
        warnings.append(f"OCR skipped: vision tool is unavailable ({exc}).")
        return warnings

    prompt = (
        "Please OCR this Xiaohongshu note image. Return only visible text, "
        "preserving line breaks where useful. If there is no text, return an empty string. "
        "Do not describe the image."
    )
    for record in image_records:
        try:
            raw = await vision_analyze_tool(
                image_url=record["local_path"],
                user_prompt=prompt,
                model=vision_model,
            )
            payload = json.loads(raw)
            if payload.get("success"):
                record["ocr_text"] = _clean_ocr_text(payload.get("analysis", ""))
                record["ocr_status"] = "ok"
            else:
                record["ocr_status"] = "failed"
                warnings.append(
                    f"OCR failed for image {record['index']}: "
                    f"{payload.get('error') or payload.get('analysis') or 'unknown error'}"
                )
        except Exception as exc:
            record["ocr_status"] = "failed"
            warnings.append(f"OCR failed for image {record['index']}: {exc}")
    return warnings


def _clean_ocr_text(value: str) -> str:
    text = str(value or "").strip()
    if text in {"无", "无文字", "没有文字", "No text", "No visible text."}:
        return ""
    return text


def _write_note_files(
    note: ParsedNote,
    image_records: list[dict[str, Any]],
    warnings: list[str],
    video_records: list[dict[str, Any]] | None = None,
    subtitle_records: list[dict[str, Any]] | None = None,
    audio_record: dict[str, Any] | None = None,
    transcript: dict[str, Any] | None = None,
) -> tuple[Path, Path, Path, dict[str, Any]]:
    note_dir = _xhs_cache_root() / _safe_note_id(note.note_id)
    note_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    video_records = video_records or []
    subtitle_records = subtitle_records or []
    audio_record = audio_record or {"source": "", "local_path": "", "extract_status": "not_applicable"}
    transcript = transcript or {
        "status": "not_applicable",
        "provider": "",
        "model": "",
        "language": "",
        "source": "",
        "text": "",
        "segments": [],
        "end_ms": None,
    }
    payload = {
        "platform": "xiaohongshu",
        "note_id": note.note_id,
        "note_type": note.note_type,
        "source_url": note.source_url,
        "resolved_url": note.resolved_url,
        "title": note.title,
        "content": note.content,
        "author": note.author,
        "stats": note.stats or _pick_stats([]),
        "likes": (note.stats or {}).get("like_count"),
        "collects": (note.stats or {}).get("collect_count"),
        "comments": (note.stats or {}).get("comment_count"),
        "shares": (note.stats or {}).get("share_count"),
        "comment_threads": note.comment_threads or _comment_empty(),
        "images": image_records,
        "videos": video_records,
        "subtitles": subtitle_records,
        "audio": audio_record,
        "transcript": transcript,
        "raw_metadata": note.raw_metadata,
        "warnings": warnings,
        "extracted_at": now,
    }
    note_json_path = note_dir / "note.json"
    note_md_path = note_dir / "note.md"
    note_json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    note_md_path.write_text(_render_note_markdown(payload), encoding="utf-8")
    return note_dir, note_json_path, note_md_path, payload


def _render_note_markdown(payload: dict[str, Any]) -> str:
    title = payload.get("title") or f"Xiaohongshu note {payload.get('note_id', '')}"
    lines = [
        f"# {title}",
        "",
        f"- platform: xiaohongshu",
        f"- note_id: {payload.get('note_id', '')}",
        f"- note_type: {payload.get('note_type', '')}",
        f"- source_url: {payload.get('source_url', '')}",
        f"- resolved_url: {payload.get('resolved_url', '')}",
        f"- extracted_at: {payload.get('extracted_at', '')}",
        "",
        "## Stats",
        "",
    ]
    stats = payload.get("stats") or {}
    if stats.get("status") == "ok":
        lines.extend(
            [
                f"- status: {stats.get('status', '')}",
                f"- source: {stats.get('source', '')}",
                f"- likes: {stats.get('like_count') if stats.get('like_count') is not None else ''}",
                f"- collects: {stats.get('collect_count') if stats.get('collect_count') is not None else ''}",
                f"- comments: {stats.get('comment_count') if stats.get('comment_count') is not None else ''}",
                f"- shares: {stats.get('share_count') if stats.get('share_count') is not None else ''}",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "- status: missing",
                "- note: interaction counts were not available in the extracted page metadata.",
                "",
            ]
        )
    lines.extend(
        [
        "## Body",
        "",
        payload.get("content") or "_No body text extracted._",
        "",
        "## Comments",
        "",
        ]
    )
    comment_threads = payload.get("comment_threads") or {}
    lines.extend(
        [
            f"- status: {comment_threads.get('status', '')}",
            f"- source: {comment_threads.get('source', '')}",
            f"- count: {comment_threads.get('count') or 0}",
            f"- has_more: {comment_threads.get('has_more') if comment_threads.get('has_more') is not None else ''}",
            f"- cursor: {comment_threads.get('cursor', '')}",
        ]
    )
    if comment_threads.get("auth_required") is not None:
        lines.append(f"- auth_required: {comment_threads.get('auth_required')}")
    if comment_threads.get("api_code") is not None:
        lines.append(f"- api_code: {comment_threads.get('api_code')}")
    if comment_threads.get("api_message"):
        lines.append(f"- api_message: {comment_threads.get('api_message')}")
    lines.append("")
    for comment in comment_threads.get("items") or []:
        author = comment.get("author") or {}
        author_name = author.get("nickname") or author.get("id") or "anonymous"
        lines.extend(
            [
                f"### Comment {comment.get('id') or ''}".rstrip(),
                "",
                f"- author: {author_name}",
                f"- like_count: {comment.get('like_count') if comment.get('like_count') is not None else ''}",
                f"- time: {comment.get('time', '')}",
                f"- ip_location: {comment.get('ip_location', '')}",
                "",
                comment.get("text") or "",
                "",
            ]
        )
        replies = comment.get("replies") or []
        if replies:
            lines.append("Replies:")
            for reply in replies:
                reply_author = (reply.get("author") or {}).get("nickname") or "anonymous"
                lines.append(f"- {reply_author}: {reply.get('text', '')}")
            lines.append("")
    lines.extend(
        [
        "## Video",
        "",
        ]
    )
    videos = payload.get("videos") or []
    if not videos:
        lines.append("_No video extracted._")
        lines.append("")
    for video in videos:
        lines.extend(
            [
                f"### Video {video.get('index', '')}",
                "",
                f"- source_url: {video.get('source_url', '')}",
                f"- local_path: {video.get('local_path', '')}",
                f"- download_status: {video.get('download_status', '')}",
                f"- duration_ms: {video.get('duration_ms') or ''}",
                f"- width: {video.get('width') or ''}",
                f"- height: {video.get('height') or ''}",
                f"- bytes: {video.get('bytes') or 0}",
                "",
            ]
        )
    subtitles = payload.get("subtitles") or []
    lines.extend(["## Subtitles", ""])
    if not subtitles:
        lines.append("_No subtitles extracted._")
        lines.append("")
    for subtitle in subtitles:
        lines.extend(
            [
                f"### Subtitle {subtitle.get('index', '')}",
                "",
                f"- source_url: {subtitle.get('source_url', '')}",
                f"- local_path: {subtitle.get('local_path', '')}",
                f"- download_status: {subtitle.get('download_status', '')}",
                f"- language: {subtitle.get('language', '')}",
                f"- char_count: {subtitle.get('char_count') or 0}",
                f"- end_ms: {subtitle.get('end_ms') or ''}",
                "",
            ]
        )
    audio = payload.get("audio") or {}
    transcript = payload.get("transcript") or {}
    lines.extend(
        [
            "## Audio",
            "",
            f"- source: {audio.get('source', '')}",
            f"- local_path: {audio.get('local_path', '')}",
            f"- extract_status: {audio.get('extract_status', '')}",
            "",
            "## Transcript",
            "",
            f"- status: {transcript.get('status', '')}",
            f"- provider: {transcript.get('provider', '')}",
            f"- model: {transcript.get('model', '')}",
            f"- language: {transcript.get('language', '')}",
            f"- source: {transcript.get('source', '')}",
            f"- end_ms: {transcript.get('end_ms') or ''}",
            f"- video_duration_ms: {transcript.get('video_duration_ms') or ''}",
            f"- coverage_ratio: {transcript.get('coverage_ratio') or ''}",
            f"- coverage_status: {transcript.get('coverage_status', '')}",
            "",
            transcript.get("text") or "_No transcript extracted._",
            "",
            "## Image OCR",
            "",
        ]
    )
    images = payload.get("images") or []
    if not images:
        lines.append("_No images downloaded._")
        lines.append("")
    for image in images:
        index = image.get("index", "")
        local_path = image.get("local_path", "")
        lines.extend(
            [
                f"### Image {index}",
                "",
                f"- role: {image.get('role', '')}",
                f"- source_url: {image.get('source_url', '')}",
                f"- local_path: {local_path}",
                f"- ocr_status: {image.get('ocr_status', '')}",
                "",
            ]
        )
        if local_path:
            lines.append(f"![image {index}]({local_path})")
            lines.append("")
        lines.append(image.get("ocr_text") or "_No OCR text extracted._")
        lines.append("")
    warnings = payload.get("warnings") or []
    if warnings:
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {item}" for item in warnings)
        lines.append("")
    return "\n".join(lines)


async def extract_xhs_note(
    url_or_text: str,
    *,
    ocr: bool = True,
    max_images: int = 9,
    vision_model: str | None = None,
    download_video: bool = True,
    transcribe: bool = True,
    stt_model: str | None = None,
    stt_language: str | None = _DEFAULT_XHS_STT_LANGUAGE,
    max_video_mb: int = _MAX_VIDEO_MB_DEFAULT,
    extract_comments: bool = True,
    max_comments: int = 1000,
) -> dict[str, Any]:
    url = _extract_xhs_url(url_or_text)
    if not url:
        raise ValueError("No supported Xiaohongshu/xhslink URL found in input.")
    max_images = max(1, min(int(max_images or 9), 18))
    max_video_mb = max(1, min(int(max_video_mb or _MAX_VIDEO_MB_DEFAULT), 500))
    max_comments = max(0, min(int(max_comments or 0), 1000))
    fetch = await _fetch_page(url)
    note = _parse_note(fetch)
    browser_metadata, browser_metadata_warnings = await _fetch_note_metadata_from_browser_cdp(note)
    if browser_metadata:
        _merge_note_metadata_from_browser_dom(note, browser_metadata)
        note.warnings.extend(browser_metadata_warnings)
    if extract_comments:
        note.comment_threads = _limit_comment_threads(note.comment_threads, max_comments)
        cdp_comment_threads, cdp_comment_warnings = await _fetch_comment_threads_from_browser_cdp(
            note,
            max_comments=max_comments,
        )
        if cdp_comment_threads.get("items"):
            note.comment_threads = _limit_comment_threads(cdp_comment_threads, max_comments)
            note.warnings.extend(cdp_comment_warnings)
        elif _comment_threads_need_api(note.comment_threads, note.stats):
            note.comment_threads = _limit_comment_threads(cdp_comment_threads, max_comments)
            note.warnings.append(
                "Comment extraction requires a logged-in Browser/CDP page; API cookie fallback is disabled."
            )
    else:
        note.comment_threads = _comment_empty(status="skipped_disabled")
    note_dir = _xhs_cache_root() / _safe_note_id(note.note_id)
    note_dir.mkdir(parents=True, exist_ok=True)
    image_records, download_warnings = await _download_note_images(note, note_dir, max_images=max_images)
    warnings = list(note.warnings) + download_warnings
    video_records, video_warnings = await _download_note_videos(
        note,
        note_dir,
        download_video=download_video,
        max_video_mb=max_video_mb,
    )
    warnings.extend(video_warnings)
    subtitle_records, subtitle_warnings = await _download_note_subtitles(note, note_dir)
    warnings.extend(subtitle_warnings)
    if note.note_type == "video":
        subtitle_transcript = _transcript_from_subtitles(
            subtitle_records,
            preferred_language=stt_language or _DEFAULT_XHS_STT_LANGUAGE,
        ) if transcribe else None
        if subtitle_transcript:
            audio_record = {
                "source": "",
                "local_path": "",
                "extract_status": "skipped_subtitle_available",
            }
            transcript = subtitle_transcript
        else:
            audio_record, transcript, transcript_warnings = await _transcribe_video_audio(
                video_records,
                note_dir,
                transcribe=transcribe,
                stt_model=stt_model,
                stt_language=stt_language or _DEFAULT_XHS_STT_LANGUAGE,
            )
            warnings.extend(transcript_warnings)
        _annotate_transcript_coverage(transcript, video_records)
    else:
        audio_record = {"source": "", "local_path": "", "extract_status": "not_applicable"}
        transcript = {
            "status": "not_applicable",
            "provider": "",
            "model": stt_model or "",
            "language": stt_language or "",
            "source": "",
            "text": "",
            "segments": [],
            "end_ms": None,
        }
    if ocr:
        warnings.extend(await _ocr_images(image_records, vision_model=vision_model))
    else:
        for record in image_records:
            record["ocr_status"] = "skipped_disabled"
    note_dir, note_json_path, note_md_path, payload = _write_note_files(
        note=note,
        image_records=image_records,
        warnings=warnings,
        video_records=video_records,
        subtitle_records=subtitle_records,
        audio_record=audio_record,
        transcript=transcript,
    )
    videos = payload.get("videos") or []
    images = payload.get("images") or []
    transcript_payload = payload.get("transcript") or {}
    transcript_text = transcript_payload.get("text") or ""
    stats_payload = payload.get("stats") or {}
    comment_threads_payload = payload.get("comment_threads") or {}
    downloaded_video_count = sum(1 for item in videos if item.get("download_status") == "ok")
    transcript_status = transcript_payload.get("status", "")
    subtitle_count = len(payload.get("subtitles") or [])
    media_summary = (
        f"{payload['note_type']} note; "
        f"{len(images)} image(s); "
        f"{downloaded_video_count}/{len(videos)} video(s) downloaded; "
        f"{subtitle_count} subtitle(s); "
        f"transcript={transcript_status} ({len(transcript_text)} chars)"
    )
    return {
        "success": True,
        "note_id": payload["note_id"],
        "note_type": payload["note_type"],
        "title": payload["title"],
        "content_chars": len(payload.get("content") or ""),
        "stats": stats_payload,
        "likes": payload.get("likes"),
        "collects": payload.get("collects"),
        "comments": payload.get("comments"),
        "shares": payload.get("shares"),
        "comment_thread_status": comment_threads_payload.get("status", ""),
        "comment_sample_count": len(comment_threads_payload.get("items") or []),
        "comment_threads": comment_threads_payload,
        "media_summary": media_summary,
        "image_count": len(images),
        "ocr_image_count": sum(1 for item in images if item.get("ocr_status") == "ok"),
        "video_count": len(videos),
        "downloaded_video_count": downloaded_video_count,
        "subtitle_count": subtitle_count,
        "video_paths": [item.get("local_path") for item in videos if item.get("download_status") == "ok" and item.get("local_path")],
        "audio_path": (payload.get("audio") or {}).get("local_path", ""),
        "transcript_status": transcript_status,
        "transcript_provider": transcript_payload.get("provider", ""),
        "transcript_language": transcript_payload.get("language", ""),
        "transcript_end_ms": transcript_payload.get("end_ms"),
        "transcript_coverage_ratio": transcript_payload.get("coverage_ratio"),
        "transcript_coverage_status": transcript_payload.get("coverage_status", ""),
        "transcript_chars": len(transcript_text),
        "transcript_preview": transcript_text[:800],
        "output_dir": str(note_dir),
        "note_json_path": str(note_json_path),
        "note_md_path": str(note_md_path),
        "source_url": payload["source_url"],
        "resolved_url": payload["resolved_url"],
        "warnings": warnings,
        "markdown_preview": _render_note_markdown(payload)[:2000],
    }


def _bool_arg(args: dict[str, Any], key: str, default: bool) -> bool:
    if key not in args or args.get(key) is None:
        return default
    value = args.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", ""}
    return bool(value)


async def xhs_extract_note_handler(args: dict[str, Any], **_: Any) -> str:
    try:
        result = await extract_xhs_note(
            args.get("url", ""),
            ocr=_bool_arg(args, "ocr", True),
            max_images=int(args.get("max_images") or 9),
            vision_model=args.get("vision_model") or None,
            download_video=_bool_arg(args, "download_video", True),
            transcribe=_bool_arg(args, "transcribe", True),
            stt_model=args.get("stt_model") or None,
            stt_language=args.get("stt_language") or _DEFAULT_XHS_STT_LANGUAGE,
            max_video_mb=int(args.get("max_video_mb") or _MAX_VIDEO_MB_DEFAULT),
            extract_comments=_bool_arg(args, "extract_comments", True),
            max_comments=int(args.get("max_comments") or 1000),
        )
        return tool_result(result)
    except Exception as exc:
        logger.warning("xhs_extract_note failed: %s", exc, exc_info=True)
        return tool_error(str(exc), success=False)
