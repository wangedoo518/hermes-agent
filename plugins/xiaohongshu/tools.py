"""Single-note Xiaohongshu note extraction tool."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import html
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse

from hermes_constants import get_hermes_dir
from tools.registry import tool_error, tool_result

logger = logging.getLogger(__name__)

XHS_EXTRACT_NOTE_SCHEMA = {
    "name": "xhs_extract_note",
    "description": (
        "Extract one user-submitted Xiaohongshu note URL. Resolves short/long "
        "links, extracts note_id/title/body/images/video metadata, downloads "
        "media into the current Hermes cache by default, optionally OCRs images, "
        "transcribes video audio by default, and writes note.json plus note.md. "
        "For full 路飞 AI Team / 路飞知识水电站 / Hermes Kanban workflows, do not "
        "call this tool directly; call lufei_ai_team_orchestrate so the Larry/"
        "Reed/Jobs/Sam/Elon Kanban cards are created first."
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

XHS_EXTRACT_PROFILE_NOTES_SCHEMA = {
    "name": "xhs_extract_profile_notes",
    "description": (
        "Extract a Xiaohongshu creator profile note inventory into profile.json "
        "and profile.md. Uses the logged-in Browser/CDP DOM when available and "
        "falls back to public SSR HTML parsing. It only reads a user-submitted "
        "profile URL and does not publish, comment, or batch-act on the account."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "A Xiaohongshu profile URL, or text containing one.",
            },
            "max_notes": {
                "type": "integer",
                "description": "Maximum profile notes to keep.",
                "default": 500,
                "minimum": 1,
                "maximum": 500,
            },
            "scroll_rounds": {
                "type": "integer",
                "description": "Browser/CDP scroll rounds before using the page app API pagination.",
                "default": 100,
                "minimum": 0,
                "maximum": 100,
            },
            "prefer_cdp": {
                "type": "boolean",
                "description": "Prefer the logged-in Browser/CDP DOM when available.",
                "default": True,
            },
            "cdp_auto_navigate": {
                "type": "boolean",
                "description": "When no matching Browser/CDP tab is open, open the profile URL through CDP and extract from that rendered DOM.",
                "default": True,
            },
        },
        "required": ["url"],
    },
}

XHS_INIT_LUFEI_WIKI_SCHEMA = {
    "name": "xhs_init_lufei_wiki",
    "description": (
        "Create the local llm-wiki skeleton for 路飞设计沉思录, including raw "
        "directories, persona.md, SCHEMA.md, index.md, and log.md."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "wiki_path": {
                "type": "string",
                "description": "Wiki root path. Defaults to LUFEI_XHS_WIKI_PATH/XHS_WIKI_PATH/WIKI_PATH or ~/Documents/develop/lufei-xhs-wiki.",
            },
            "overwrite": {
                "type": "boolean",
                "description": "Overwrite existing template files, but never delete user content.",
                "default": False,
            },
        },
    },
}

XHS_INGEST_NOTE_TO_WIKI_SCHEMA = {
    "name": "xhs_ingest_note_to_wiki",
    "description": (
        "Ingest one extracted Xiaohongshu note.json/note.md artifact into the "
        "路飞 llm-wiki raw/xhs/notes area with frontmatter, transcript, comments, "
        "source provenance, and sha256 drift metadata."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "note_json_path": {
                "type": "string",
                "description": "Path to note.json produced by xhs_extract_note.",
            },
            "note_md_path": {
                "type": "string",
                "description": "Optional path to note.md produced by xhs_extract_note.",
            },
            "wiki_path": {
                "type": "string",
                "description": "Wiki root path. Defaults to LUFEI_XHS_WIKI_PATH/XHS_WIKI_PATH/WIKI_PATH or ~/Documents/develop/lufei-xhs-wiki.",
            },
            "account": {
                "type": "string",
                "description": "Creator account name for frontmatter.",
                "default": "路飞设计沉思录",
            },
        },
        "required": ["note_json_path"],
    },
}

XHS_INGEST_ACCOUNT_TO_WIKI_SCHEMA = {
    "name": "xhs_ingest_account_to_wiki",
    "description": (
        "Ingest one xhs_extract_profile_notes profile.json artifact into the "
        "路飞 llm-wiki raw/xhs/profile area. This turns a creator account "
        "inventory into Obsidian-readable markdown and a machine-readable "
        "profile.json source."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "profile_json_path": {
                "type": "string",
                "description": "Path to profile.json produced by xhs_extract_profile_notes.",
            },
            "profile_md_path": {
                "type": "string",
                "description": "Optional path to profile.md produced by xhs_extract_profile_notes.",
            },
            "wiki_path": {
                "type": "string",
                "description": "Wiki root path. Defaults to the configured wiki path or the active Obsidian vault/lufei-xhs-wiki.",
            },
            "account": {
                "type": "string",
                "description": "Creator account name.",
                "default": "路飞设计沉思录",
            },
        },
        "required": ["profile_json_path"],
    },
}

XHS_BUILD_WIKI_MANIFEST_SCHEMA = {
    "name": "xhs_build_wiki_manifest",
    "description": (
        "Scan the 路飞 llm-wiki markdown files and write _derived/manifest.json "
        "so downstream skills can consume wiki context without a special wiki API."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "wiki_path": {
                "type": "string",
                "description": "Wiki root path.",
            },
        },
    },
}

XHS_QUERY_WIKI_CONTEXT_SCHEMA = {
    "name": "xhs_query_wiki_context",
    "description": (
        "Read relevant 路飞 llm-wiki markdown context for a topic/script task. "
        "This is a file-based adapter over llm-wiki, not a database API."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Query such as 压力面、字节 UI/UX、作品集追问.",
            },
            "wiki_path": {
                "type": "string",
                "description": "Wiki root path.",
            },
            "max_files": {
                "type": "integer",
                "default": 8,
                "minimum": 1,
                "maximum": 30,
            },
            "max_chars": {
                "type": "integer",
                "default": 12000,
                "minimum": 1000,
                "maximum": 50000,
            },
        },
        "required": ["query"],
    },
}

XHS_OPEN_WIKI_IN_OBSIDIAN_SCHEMA = {
    "name": "xhs_open_wiki_in_obsidian",
    "description": (
        "Open the 路飞 wiki in Obsidian and return vault/CLI status. This is a "
        "thin Obsidian management adapter; extraction and skill logic still "
        "reads/writes files directly."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "wiki_path": {
                "type": "string",
                "description": "Wiki root path.",
            },
            "target_path": {
                "type": "string",
                "description": "Wiki-relative file to open.",
                "default": "index.md",
            },
        },
    },
}

XHS_RUN_CONTENT_SKILL_SCHEMA = {
    "name": "xhs_run_content_skill",
    "description": (
        "Run one local xhs-content-pipeline skill (viral-analysis, topic-selection, "
        "script-generation, or comment-intelligence) with text input. This bridges "
        "Hermes extraction/wiki artifacts into the existing content skill pipeline."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "skill": {
                "type": "string",
                "description": "Skill name: viral-analysis, topic-selection, script-generation, or comment-intelligence.",
            },
            "input_text": {
                "type": "string",
                "description": "Input text for the skill.",
            },
            "input_path": {
                "type": "string",
                "description": "Optional file path to read input text from.",
            },
            "output_path": {
                "type": "string",
                "description": "Optional file path to save the skill output.",
            },
            "pipeline_path": {
                "type": "string",
                "description": "Path to xhs-content-pipeline. Defaults to /Users/champion/Documents/develop/skills/xhs-content-pipeline.",
            },
            "model": {
                "type": "string",
                "description": "Optional model override passed to run_skill.py.",
            },
        },
        "required": ["skill"],
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


def _extract_profile_id_from_url(value: str) -> str | None:
    try:
        parsed = urlparse(value)
    except Exception:
        return None
    path = parsed.path or ""
    match = re.search(r"/user/profile/([^/?#]+)", path)
    if match:
        return match.group(1)
    query = parse_qs(parsed.query or "")
    for key in ("user_id", "userId", "id"):
        for item in query.get(key, []):
            if item:
                return item
    return None


def _is_xhs_profile_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    return (
        (host == "xiaohongshu.com" or host.endswith(".xiaohongshu.com"))
        and "/user/profile/" in (parsed.path or "")
    )


def _safe_note_id(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_")
    return cleaned[:80] or hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _xhs_cache_root() -> Path:
    root = get_hermes_dir("cache/xiaohongshu", "xhs_cache")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _xhs_profile_cache_root() -> Path:
    root = _xhs_cache_root() / "profiles"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _run_obsidian_cli(args: list[str], *, timeout: float = 8.0) -> tuple[bool, str, str]:
    executable = shutil.which("obsidian")
    if not executable:
        return (False, "", "obsidian command not found")
    try:
        proc = subprocess.run(
            [executable, *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except Exception as exc:
        return (False, "", str(exc))
    return (proc.returncode == 0, proc.stdout.strip(), proc.stderr.strip())


def _obsidian_active_vault_path() -> Path | None:
    configured = (os.environ.get("OBSIDIAN_VAULT_PATH") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    ok, stdout, _stderr = _run_obsidian_cli(["vault", "info=path"], timeout=5.0)
    if not ok or not stdout:
        return None
    first_line = stdout.splitlines()[0].strip()
    return Path(first_line).expanduser().resolve() if first_line else None


def _obsidian_status(wiki_path: Path) -> dict[str, Any]:
    cli_available = shutil.which("obsidian") is not None
    vault_path = _obsidian_active_vault_path() if cli_available else None
    wiki_path = wiki_path.resolve()
    inside_vault = False
    wiki_relative_path = ""
    if vault_path:
        with contextlib.suppress(ValueError):
            wiki_relative_path = wiki_path.relative_to(vault_path).as_posix()
            inside_vault = True
    return {
        "cli_available": cli_available,
        "active_vault_path": str(vault_path) if vault_path else "",
        "wiki_path": str(wiki_path),
        "inside_active_vault": inside_vault,
        "wiki_relative_path": wiki_relative_path,
    }


def _default_lufei_wiki_path() -> Path:
    configured = (
        os.environ.get("LUFEI_XHS_WIKI_PATH")
        or os.environ.get("XHS_WIKI_PATH")
        or os.environ.get("WIKI_PATH")
    )
    if configured:
        return Path(configured).expanduser()
    vault_path = _obsidian_active_vault_path()
    if vault_path:
        return vault_path / "lufei-xhs-wiki"
    return Path.home() / "Documents" / "develop" / "lufei-xhs-wiki"


def _resolve_wiki_path(value: str | None = None) -> Path:
    path = Path(value).expanduser() if value else _default_lufei_wiki_path()
    return path.resolve()


def _json_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _markdown_hash(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


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


def _extract_profile_stats_from_node(node: dict[str, Any], source: str = "profile_json") -> dict[str, Any] | None:
    """Pick interaction stats from a profile note node, including nested interactInfo."""
    candidates: list[dict[str, Any]] = []
    preferred_keys = {
        "interactinfo",
        "interact_info",
        "interactioninfo",
        "interaction_info",
        "statistics",
        "stats",
        "stat",
    }
    direct = _extract_stats_from_node(node, source=source)
    if direct:
        candidates.append(direct)
    for key, value in node.items():
        if isinstance(value, dict) and _normalised_key(key) in preferred_keys:
            nested = _extract_stats_from_node(value, source=f"{source}:{key}")
            if nested:
                candidates.append(nested)
    if not candidates:
        for child in _walk_json(node):
            if child is node:
                continue
            nested = _extract_stats_from_node(child, source=source)
            if nested:
                candidates.append(nested)
    return _pick_stats(candidates) if candidates else None


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


def _profile_note_url(note_id: str, xsec_token: str = "", xsec_source: str = "pc_note") -> str:
    base = f"https://www.xiaohongshu.com/explore/{note_id}"
    query: dict[str, str] = {}
    if xsec_token:
        query["xsec_token"] = xsec_token
    if xsec_source:
        query["xsec_source"] = xsec_source
    if query:
        return f"{base}?{urlencode(query)}"
    return base


def _extract_xsec_token_from_url(value: str) -> str:
    try:
        parsed = urlparse(value)
    except Exception:
        return ""
    return (parse_qs(parsed.query or {}).get("xsec_token") or [""])[0]


def _profile_note_record(
    *,
    note_id: str,
    source: str,
    url: str = "",
    title: str = "",
    note_type: str = "",
    stats: dict[str, Any] | None = None,
    xsec_token: str = "",
    cover_url: str = "",
    publish_time: str = "",
    author: dict[str, str] | None = None,
) -> dict[str, Any]:
    note_id = (note_id or "").strip().lower()
    xsec_token = xsec_token or _extract_xsec_token_from_url(url)
    return {
        "note_id": note_id,
        "url": url or _profile_note_url(note_id, xsec_token=xsec_token),
        "xsec_token": xsec_token,
        "title": _clean_title(title),
        "note_type": note_type or "unknown",
        "cover_url": cover_url,
        "publish_time": _clean_text(publish_time),
        "stats": stats or _pick_stats([]),
        "likes": (stats or {}).get("like_count"),
        "collects": (stats or {}).get("collect_count"),
        "comments": (stats or {}).get("comment_count"),
        "shares": (stats or {}).get("share_count"),
        "author": author or {},
        "source": source,
    }


def _merge_profile_note_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for record in records:
        note_id = (record.get("note_id") or "").lower()
        if not note_id:
            continue
        old = merged.get(note_id)
        if old is None:
            merged[note_id] = dict(record)
            continue
        if not old.get("title") and record.get("title"):
            old["title"] = record["title"]
        if not old.get("url") and record.get("url"):
            old["url"] = record["url"]
        if not old.get("xsec_token") and record.get("xsec_token"):
            old["xsec_token"] = record["xsec_token"]
        if old.get("note_type") in {"", "unknown"} and record.get("note_type"):
            old["note_type"] = record["note_type"]
        if not old.get("cover_url") and record.get("cover_url"):
            old["cover_url"] = record["cover_url"]
        if not old.get("publish_time") and record.get("publish_time"):
            old["publish_time"] = record["publish_time"]
        old_stats = old.get("stats") or _pick_stats([])
        new_stats = record.get("stats") or _pick_stats([])
        old["stats"] = _pick_stats([old_stats, new_stats])
        for key, stat_key in (
            ("likes", "like_count"),
            ("collects", "collect_count"),
            ("comments", "comment_count"),
            ("shares", "share_count"),
        ):
            old[key] = (old["stats"] or {}).get(stat_key)
        if not old.get("author") and record.get("author"):
            old["author"] = record["author"]
        sources = set(filter(None, str(old.get("source") or "").split(",")))
        if record.get("source"):
            sources.add(str(record["source"]))
        old["source"] = ",".join(sorted(sources))
    return list(merged.values())


def _profile_note_title_from_node(node: dict[str, Any]) -> str:
    title_keys = {
        "displaytitle",
        "display_title",
        "title",
        "headline",
        "name",
        "notetitle",
        "note_title",
    }
    fallback_keys = {"desc", "description", "content", "notetext", "note_text", "text"}
    for key, value in node.items():
        key_n = _normalised_key(key)
        if key_n in {item.replace("_", "") for item in title_keys} and isinstance(value, str):
            title = _clean_title(value)
            if title:
                return title
    for key, value in node.items():
        key_n = _normalised_key(key)
        if key_n in {item.replace("_", "") for item in fallback_keys} and isinstance(value, str):
            title = _clean_title(value)
            if title:
                return title[:80]
    return ""


def _profile_note_type_from_node(node: dict[str, Any]) -> str:
    for key, value in node.items():
        key_n = _normalised_key(key)
        if key_n in {"type", "notetype", "modeltype"} and value is not None:
            text = _clean_text(value).lower()
            if "video" in text or text in {"1", "视频"}:
                return "video"
            if "normal" in text or "image" in text or text in {"0", "图文"}:
                return "image_text"
    for child in _walk_json(node):
        for value in child.values():
            if isinstance(value, str) and _normalise_video_url(value):
                return "video"
    return "unknown"


def _profile_author_from_node(node: dict[str, Any]) -> dict[str, str]:
    for key, value in node.items():
        if _normalised_key(key) in {"user", "userinfo", "author", "user_info"} and isinstance(value, dict):
            author = _extract_comment_user(value)
            if author:
                return author
    return {}


def _profile_cover_from_node(node: dict[str, Any], base_url: str) -> str:
    for key, value in node.items():
        key_l = str(key).lower()
        if not any(marker in key_l for marker in ("cover", "image", "thumbnail", "poster")):
            continue
        if isinstance(value, str):
            cover = _normalise_image_url(value, base_url=base_url)
            if cover:
                return cover
        if isinstance(value, (dict, list)):
            for child in _walk_json(value):
                for child_value in child.values():
                    if isinstance(child_value, str):
                        cover = _normalise_image_url(child_value, base_url=base_url)
                        if cover:
                            return cover
    return ""


def _profile_publish_time_from_node(node: dict[str, Any]) -> str:
    for key, value in node.items():
        if _normalised_key(key) in {"time", "date", "publishtime", "publish_time", "createtime", "create_time"}:
            text = _clean_text(value)
            if text:
                return text
    return ""


def _extract_profile_note_records_from_json(blobs: list[Any], base_url: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    note_id_keys = {"noteid", "note_id", "notecardid", "id"}
    url_keys = {"url", "link", "href", "shareurl", "weburl"}
    for blob in blobs:
        for node in _walk_json(blob):
            if not isinstance(node, dict):
                continue
            note_id = ""
            url = ""
            for key, value in node.items():
                key_n = _normalised_key(key)
                if key_n in {item.replace("_", "") for item in note_id_keys} and isinstance(value, str):
                    matched = _NOTE_ID_RE.search(value)
                    if matched:
                        note_id = matched.group(1).lower()
                if key_n in url_keys and isinstance(value, str) and "/explore/" in value:
                    url = urljoin(base_url, html.unescape(value))
                    matched = _extract_note_id_from_url(url)
                    if matched:
                        note_id = matched
            if not note_id:
                for value in node.values():
                    if isinstance(value, str) and "/explore/" in value:
                        url = urljoin(base_url, html.unescape(value))
                        note_id = _extract_note_id_from_url(url) or ""
                        if note_id:
                            break
            if not note_id:
                continue
            stats = _extract_profile_stats_from_node(node, source="profile_json")
            records.append(
                _profile_note_record(
                    note_id=note_id,
                    source="json",
                    url=url,
                    title=_profile_note_title_from_node(node),
                    note_type=_profile_note_type_from_node(node),
                    stats=stats,
                    cover_url=_profile_cover_from_node(node, base_url),
                    publish_time=_profile_publish_time_from_node(node),
                    author=_profile_author_from_node(node),
                )
            )
    return _merge_profile_note_records(records)


def _extract_profile_note_records_from_html_anchors(html_text: str, base_url: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    anchor_re = re.compile(r"<a\b([^>]*)>(.*?)</a\s*>", re.IGNORECASE | re.DOTALL)
    for raw_attrs, inner_html in anchor_re.findall(html_text or ""):
        attrs = {
            key.lower(): html.unescape(value.strip())
            for key, _quote, value in _ATTR_RE.findall(raw_attrs)
        }
        href = attrs.get("href") or ""
        if "/explore/" not in href:
            continue
        url = urljoin(base_url, href)
        note_id = _extract_note_id_from_url(url)
        if not note_id:
            continue
        title = attrs.get("title") or attrs.get("aria-label") or _clean_text(inner_html)
        records.append(
            _profile_note_record(
                note_id=note_id,
                source="html:a",
                url=url,
                title=title,
                xsec_token=_extract_xsec_token_from_url(url),
            )
        )
    return _merge_profile_note_records(records)


def _extract_profile_metadata_from_html(html_text: str, base_url: str) -> dict[str, Any]:
    meta = _meta_tags(html_text)
    title = _clean_title(meta.get("og:title") or _html_title(html_text))
    description = _clean_text(meta.get("og:description") or meta.get("description") or "")
    return {
        "platform": "xiaohongshu",
        "profile_id": _extract_profile_id_from_url(base_url) or "",
        "profile_url": base_url,
        "nickname": title,
        "description": description,
    }


def _extract_profile_notes_from_html(html_text: str, base_url: str) -> dict[str, Any]:
    blobs = _iter_json_blobs(html_text)
    json_records = _extract_profile_note_records_from_json(blobs, base_url)
    anchor_records = _extract_profile_note_records_from_html_anchors(html_text, base_url)
    return {
        "profile": _extract_profile_metadata_from_html(html_text, base_url),
        "notes": _merge_profile_note_records(json_records + anchor_records),
        "sources": {
            "json_count": len(json_records),
            "anchor_count": len(anchor_records),
        },
    }


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
        or ""
    ).strip()
    if not raw:
        with contextlib.suppress(Exception):
            from tools.browser_tool import _get_cdp_override  # type: ignore[import-not-found]

            raw = (_get_cdp_override() or "").strip()
    if not raw:
        raw = "http://127.0.0.1:9222"
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


def _find_profile_cdp_target(
    targets: list[dict[str, Any]],
    *,
    profile_id: str,
) -> dict[str, Any] | None:
    pages = [
        item
        for item in targets
        if item.get("type") == "page" and item.get("webSocketDebuggerUrl")
    ]
    target = next(
        (
            item
            for item in pages
            if profile_id and profile_id in str(item.get("url", ""))
        ),
        None,
    )
    if target is not None:
        return target
    return next(
        (
            item
            for item in pages
            if "xiaohongshu.com/user/profile/" in str(item.get("url", ""))
        ),
        None,
    )


async def _open_cdp_target(cdp_base_url: str, url: str) -> dict[str, Any] | None:
    import httpx

    encoded_url = quote(url, safe="")
    endpoints = [
        f"{cdp_base_url.rstrip('/')}/json/new?{encoded_url}",
        f"{cdp_base_url.rstrip('/')}/json/new?url={encoded_url}",
    ]
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=False, trust_env=False) as client:
            for endpoint in endpoints:
                for method in ("put", "get"):
                    try:
                        response = await getattr(client, method)(endpoint)
                    except Exception:
                        continue
                    if response.status_code >= 400:
                        continue
                    try:
                        data = response.json()
                    except Exception:
                        continue
                    if isinstance(data, dict) and data.get("webSocketDebuggerUrl"):
                        return data
    except Exception:
        return None
    return None


_CDP_PAGE_READY_JS = r"""
(() => new Promise((resolve) => {
  const done = () => resolve({
    href: location.href,
    title: document.title,
    readyState: document.readyState,
    bodyText: String(document.body && document.body.innerText || '').slice(0, 500)
  });
  if (document.readyState === 'complete' || document.readyState === 'interactive') {
    setTimeout(done, 800);
    return;
  }
  window.addEventListener('load', () => setTimeout(done, 800), {once: true});
  setTimeout(done, 4500);
}))()
"""


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


def _profile_dom_extractor_js(max_notes: int, scroll_rounds: int) -> str:
    max_notes = max(1, min(int(max_notes or 500), 500))
    scroll_rounds = max(0, min(int(scroll_rounds or 100), 100))
    return (
        r"""
(async () => {
  const maxNotes = __MAX_NOTES__;
  const scrollRounds = __SCROLL_ROUNDS__;
  const sleep = (ms) => new Promise((done) => setTimeout(done, ms));
  const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const numberLike = (value) => {
    const text = clean(value).replace(/,/g, '');
    if (!text) return null;
    const match = text.match(/(\d+(?:\.\d+)?)(万|千|k|K|w|W)?/);
    if (!match) return null;
    const base = Number(match[1]);
    if (!Number.isFinite(base)) return null;
    const suffix = match[2];
    if (suffix === '万' || suffix === 'w' || suffix === 'W') return Math.round(base * 10000);
    if (suffix === '千' || suffix === 'k' || suffix === 'K') return Math.round(base * 1000);
    return Math.round(base);
  };
  const unwrap = (value) => {
    if (value && typeof value === 'object' && ('_rawValue' in value || '_value' in value)) {
      return value._rawValue ?? value._value;
    }
    return value;
  };
  const coverUrlFrom = (cover) => {
    if (!cover || typeof cover !== 'object') return '';
    if (cover.urlDefault || cover.urlPre || cover.url) return cover.urlDefault || cover.urlPre || cover.url || '';
    const infoList = Array.isArray(cover.infoList) ? cover.infoList : [];
    const preferred = infoList.find((item) => item && (item.imageScene === 'WB_DFT' || item.image_scene === 'WB_DFT')) ||
      infoList.find((item) => item && item.url);
    return preferred?.url || '';
  };
  const noteIdFromHref = (href) => {
    const text = String(href || '');
    const exploreMatch = text.match(/\/explore\/([a-f0-9]{24})/i);
    if (exploreMatch) return exploreMatch[1].toLowerCase();
    const profileMatch = text.match(/\/user\/profile\/[a-f0-9]{24}\/([a-f0-9]{24})/i);
    if (profileMatch) return profileMatch[1].toLowerCase();
    return '';
  };
  const tokenFromHref = (href) => {
    try {
      return new URL(href, location.href).searchParams.get('xsec_token') || '';
    } catch (_) {
      return '';
    }
  };
  const profileId = (location.pathname.match(/\/user\/profile\/([^/?#]+)/) || [])[1] || '';
  const profileXsecToken = (() => {
    try { return new URL(location.href).searchParams.get('xsec_token') || ''; } catch (_) { return ''; }
  })();
  const profileXsecSource = (() => {
    try { return new URL(location.href).searchParams.get('xsec_source') || 'pc_user'; } catch (_) { return 'pc_user'; }
  })();
  const buildNoteUrl = (note_id, xsec_token) => {
    const params = new URLSearchParams();
    if (xsec_token) params.set('xsec_token', xsec_token);
    params.set('xsec_source', 'pc_user');
    return new URL(`/explore/${note_id}?${params.toString()}`, location.href).toString();
  };
  const extractFromCard = (anchor) => {
    const href = anchor.getAttribute('href') || anchor.href || '';
    const note_id = noteIdFromHref(href);
    if (!note_id) return null;
    const card = anchor.closest('.note-item, .cover, section, li, div') || anchor;
    const allHrefs = Array.from(card.querySelectorAll('a[href]')).map((item) => item.getAttribute('href') || item.href || '');
    const detailHref = allHrefs.find((item) => item.includes(note_id) && item.includes('xsec_token=')) ||
      allHrefs.find((item) => item.includes(note_id)) ||
      href;
    const title = clean(
      card.querySelector('.title')?.innerText ||
      card.querySelector('.note-title')?.innerText ||
      card.querySelector('[class*="title"]')?.innerText ||
      anchor.getAttribute('title') ||
      anchor.getAttribute('aria-label') ||
      anchor.innerText
    );
    const countText = clean(
      card.querySelector('.like-wrapper .count')?.innerText ||
      card.querySelector('[class*="like"] [class*="count"]')?.innerText ||
      card.querySelector('.count')?.innerText ||
      ''
    );
    const numericLines = String(card.innerText || '').split('\n').map(clean).filter((line) => /^\d+(?:\.\d+)?(?:万|千|k|K|w|W)?$/.test(line));
    const likeText = countText || numericLines.at(-1) || '';
    const cover = card.querySelector('img')?.src || '';
    const video = !!card.querySelector('video, [class*="video"], .play-icon');
    const xsec_token = tokenFromHref(detailHref);
    return {
      note_id,
      url: buildNoteUrl(note_id, xsec_token),
      xsec_token,
      title,
      note_type: video ? 'video' : 'unknown',
      cover_url: cover,
      stats: {
        status: likeText ? 'ok' : 'missing',
        source: 'browser_cdp:profile_dom',
        like_count: likeText ? numberLike(likeText) : null,
        collect_count: null,
        comment_count: null,
        share_count: null,
        liked: null,
        collected: null
      },
      source: 'browser_cdp:profile_dom'
    };
  };
  const extractFromApiNote = (raw, source) => {
    if (!raw || typeof raw !== 'object') return null;
    const card = raw.noteCard || raw.note_card || raw.card || raw;
    const note_id = clean(card.noteId || card.note_id || raw.noteId || raw.note_id || raw.id).toLowerCase();
    if (!/^[a-f0-9]{24}$/.test(note_id)) return null;
    const xsec_token = clean(card.xsecToken || card.xsec_token || raw.xsecToken || raw.xsec_token);
    const title = clean(card.displayTitle || card.display_title || card.title || raw.displayTitle || raw.title);
    const typeText = clean(card.type || raw.type).toLowerCase();
    const statsRaw = card.interactInfo || card.interact_info || raw.interactInfo || raw.interact_info || {};
    const likeText = clean(statsRaw.likedCount ?? statsRaw.likeCount ?? statsRaw.likes ?? statsRaw.liked_count ?? '');
    const collectText = clean(statsRaw.collectedCount ?? statsRaw.collectCount ?? statsRaw.collect_count ?? '');
    const commentText = clean(statsRaw.commentCount ?? statsRaw.comments ?? statsRaw.comment_count ?? '');
    const shareText = clean(statsRaw.shareCount ?? statsRaw.shares ?? statsRaw.share_count ?? '');
    return {
      note_id,
      url: buildNoteUrl(note_id, xsec_token),
      xsec_token,
      title,
      note_type: typeText === 'video' ? 'video' : (typeText === 'normal' ? 'normal' : (typeText || 'unknown')),
      cover_url: coverUrlFrom(card.cover || raw.cover),
      stats: {
        status: likeText || collectText || commentText || shareText ? 'ok' : 'missing',
        source,
        like_count: likeText ? numberLike(likeText) : null,
        collect_count: collectText ? numberLike(collectText) : null,
        comment_count: commentText ? numberLike(commentText) : null,
        share_count: shareText ? numberLike(shareText) : null,
        liked: typeof statsRaw.liked === 'boolean' ? statsRaw.liked : null,
        collected: typeof statsRaw.collected === 'boolean' ? statsRaw.collected : null
      },
      author: card.user || raw.user || {},
      source
    };
  };
  const stateUser = window.__INITIAL_STATE__?.user || {};
  const stateNotes = () => {
    const groups = unwrap(stateUser.notes) || [];
    if (!Array.isArray(groups)) return [];
    return groups.flatMap((group) => Array.isArray(group) ? group : [])
      .map((item) => extractFromApiNote(item, 'browser_cdp:initial_state'))
      .filter(Boolean);
  };
  const noteQuery = () => {
    const queries = unwrap(stateUser.noteQueries) || [];
    return Array.isArray(queries) ? (queries[0] || {}) : {};
  };
  const collect = () => Array.from(document.querySelectorAll('a[href*="/explore/"]'))
    .map(extractFromCard)
    .filter(Boolean);
  const merged = new Map();
  const mergeItems = (items) => {
    for (const item of items || []) {
      const old = merged.get(item.note_id) || {};
      const sources = String([old.source, item.source].filter(Boolean).join(',')).split(',').filter(Boolean);
      merged.set(item.note_id, {
        ...old,
        ...item,
        title: old.title || item.title,
        url: item.url || old.url,
        xsec_token: item.xsec_token || old.xsec_token,
        cover_url: item.cover_url || old.cover_url,
        stats: item.stats && item.stats.status !== 'missing' ? item.stats : (old.stats || item.stats),
        source: Array.from(new Set(sources)).sort().join(',')
      });
    }
  };
  const merge = () => {
    mergeItems(stateNotes());
    mergeItems(collect());
  };
  merge();
  let stableRounds = 0;
  let lastCount = merged.size;
  for (let round = 0; round < scrollRounds && merged.size < maxNotes; round += 1) {
    window.scrollBy(0, Math.max(window.innerHeight * 0.85, 700));
    document.scrollingElement && (document.scrollingElement.scrollTop += Math.max(window.innerHeight * 0.85, 700));
    window.dispatchEvent(new WheelEvent('wheel', {deltaY: 900, bubbles: true}));
    await sleep(700);
    merge();
    if (merged.size === lastCount) {
      stableRounds += 1;
    } else {
      stableRounds = 0;
      lastCount = merged.size;
    }
    if (stableRounds >= 5) break;
  }
  let appApiPages = 0;
  let appApiError = '';
  let hasMore = !!noteQuery().hasMore;
  let cursor = clean(noteQuery().cursor || '');
  const getWebpackRequire = () => {
    let req;
    try {
      window.webpackChunkxhs_pc_web?.push([[Math.floor(Math.random() * 100000000)], {}, (runtimeRequire) => { req = runtimeRequire; }]);
    } catch (_) {}
    return req;
  };
  const fetchAppPage = async () => {
    const req = getWebpackRequire();
    if (!req) throw new Error('webpack runtime is unavailable');
    const api = req('40122');
    const fn = api && (api.t8 || api.Ff);
    if (typeof fn !== 'function') throw new Error('user_posted app API wrapper is unavailable');
    return await fn({
      params: {
        num: 30,
        cursor,
        user_id: profileId,
        image_formats: 'jpg,webp,avif',
        xsec_token: profileXsecToken,
        xsec_source: profileXsecSource
      }
    });
  };
  for (let page = 0; page < 30 && hasMore && cursor && merged.size < maxNotes; page += 1) {
    try {
      const response = await fetchAppPage();
      const pageNotes = Array.isArray(response?.notes) ? response.notes : [];
      mergeItems(pageNotes.map((item) => extractFromApiNote(item, 'browser_cdp:app_api')).filter(Boolean));
      appApiPages += 1;
      hasMore = !!response?.hasMore;
      cursor = clean(response?.cursor || '');
      await sleep(300);
      if (!pageNotes.length || !cursor) break;
    } catch (error) {
      appApiError = String(error && error.message ? error.message : error);
      break;
    }
  }
  const profile = {
    platform: 'xiaohongshu',
    profile_id: profileId,
    profile_url: location.href,
    nickname: clean(document.querySelector('.user-name, .user-nickname, .name, h1')?.innerText || document.title.replace(/- 小红书.*/, '')),
    description: clean(document.querySelector('.user-desc, .desc, [class*="desc"]')?.innerText || '')
  };
  return {
    profile,
    notes: Array.from(merged.values()).slice(0, maxNotes),
    stable_rounds: stableRounds,
    app_api_pages: appApiPages,
    has_more: hasMore,
    cursor,
    app_api_error: appApiError
  };
})()
"""
    ).replace("__MAX_NOTES__", str(max_notes)).replace("__SCROLL_ROUNDS__", str(scroll_rounds))


def _parse_profile_dom_payload(payload: dict[str, Any], source: str = "browser_cdp:profile_dom") -> dict[str, Any]:
    profile = payload.get("profile") if isinstance(payload, dict) else {}
    raw_notes = payload.get("notes") if isinstance(payload, dict) else []
    notes: list[dict[str, Any]] = []
    if isinstance(raw_notes, list):
        for item in raw_notes:
            if not isinstance(item, dict):
                continue
            note_id = _extract_note_id_from_url(item.get("url", "")) or _clean_text(item.get("note_id", "")).lower()
            if not note_id:
                continue
            stats = item.get("stats") if isinstance(item.get("stats"), dict) else None
            record_source = _clean_text(item.get("source") or source) or source
            notes.append(
                _profile_note_record(
                    note_id=note_id,
                    source=record_source,
                    url=item.get("url", ""),
                    title=item.get("title", ""),
                    note_type=item.get("note_type", ""),
                    stats=stats,
                    xsec_token=item.get("xsec_token", ""),
                    cover_url=item.get("cover_url", ""),
                    publish_time=item.get("publish_time", ""),
                    author=item.get("author") if isinstance(item.get("author"), dict) else {},
                )
            )
    return {
        "profile": profile if isinstance(profile, dict) else {},
        "notes": _merge_profile_note_records(notes),
        "stable_rounds": payload.get("stable_rounds") if isinstance(payload, dict) else None,
        "app_api_pages": payload.get("app_api_pages") if isinstance(payload, dict) else None,
        "has_more": payload.get("has_more") if isinstance(payload, dict) else None,
        "cursor": payload.get("cursor") if isinstance(payload, dict) else None,
        "app_api_error": payload.get("app_api_error") if isinstance(payload, dict) else "",
    }


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


async def _fetch_profile_notes_from_browser_cdp(
    profile_url: str,
    *,
    max_notes: int,
    scroll_rounds: int,
    auto_navigate: bool = True,
) -> tuple[dict[str, Any], list[str]]:
    cdp_base_url = _cdp_http_base_url()
    if not cdp_base_url:
        return ({}, [])

    warnings: list[str] = []
    targets = await _fetch_cdp_targets(cdp_base_url)
    profile_id = _extract_profile_id_from_url(profile_url) or ""
    target = _find_profile_cdp_target(targets, profile_id=profile_id)
    if target is None:
        if not auto_navigate:
            return ({}, [])
        target = await _open_cdp_target(cdp_base_url, profile_url)
        if target is None:
            return (
                {},
                [
                    "Browser/CDP is configured but no matching profile tab was open, and opening a new CDP target failed. Start a local Chrome with --remote-debugging-port or configure BROWSER_CDP_URL."
                ],
            )
        warnings.append("Profile page was opened through Browser/CDP because no matching tab was already available.")
        await _browser_cdp_runtime_evaluate(
            str(target.get("webSocketDebuggerUrl")),
            _CDP_PAGE_READY_JS,
            await_promise=True,
        )
        refreshed = await _fetch_cdp_targets(cdp_base_url)
        target = _find_profile_cdp_target(refreshed, profile_id=profile_id) or target

    payload = await _browser_cdp_runtime_evaluate(
        str(target.get("webSocketDebuggerUrl")),
        _profile_dom_extractor_js(max_notes=max_notes, scroll_rounds=scroll_rounds),
        await_promise=True,
    )
    parsed = _parse_profile_dom_payload(payload if isinstance(payload, dict) else {})
    if parsed.get("notes"):
        api_pages = parsed.get("app_api_pages") or 0
        source_detail = "DOM/initial state"
        if api_pages:
            source_detail += f" plus {api_pages} page-app API page(s)"
        warnings.append(
            f"Profile notes were extracted from the logged-in Browser/CDP {source_detail} ({len(parsed.get('notes') or [])} notes)."
        )
        if parsed.get("has_more"):
            warnings.append(
                "Profile pagination still reports has_more=true after extraction; increase max_notes or retry in the logged-in browser if you need more."
            )
        if parsed.get("app_api_error"):
            warnings.append(f"Profile page-app API pagination stopped: {parsed.get('app_api_error')}")
    elif target:
        warnings.append(
            "Browser/CDP opened or found the profile page, but no visible note cards were extracted from the rendered DOM."
        )
    return (parsed, warnings)


async def _fetch_comment_threads_from_api(
    note: ParsedNote,
    *,
    max_comments: int,
    cookie: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    max_comments = max(0, min(int(max_comments or 0), 1000))
    if max_comments <= 0:
        return (_comment_empty(status="skipped_limit", source="api:sns/web/v2/comment/page"), [])

    cookie = (_xhs_cookie_from_env() if cookie is None else cookie).strip()
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


def _render_profile_markdown(payload: dict[str, Any]) -> str:
    profile = payload.get("profile") or {}
    notes = payload.get("notes") or []
    lines = [
        f"# {profile.get('nickname') or profile.get('profile_id') or 'Xiaohongshu profile'}",
        "",
        f"- platform: xiaohongshu",
        f"- profile_id: {profile.get('profile_id', '')}",
        f"- profile_url: {profile.get('profile_url', '')}",
        f"- extracted_at: {payload.get('extracted_at', '')}",
        f"- note_count: {len(notes)}",
        "",
        "## Description",
        "",
        profile.get("description") or "_No profile description extracted._",
        "",
        "## Notes",
        "",
        "| # | note_id | title | type | likes | collects | comments |",
        "|---:|---|---|---|---:|---:|---:|",
    ]
    for index, note in enumerate(notes, start=1):
        lines.append(
            "| {index} | [{note_id}]({url}) | {title} | {note_type} | {likes} | {collects} | {comments} |".format(
                index=index,
                note_id=note.get("note_id", ""),
                url=note.get("url", ""),
                title=(note.get("title") or "").replace("|", "\\|"),
                note_type=note.get("note_type", ""),
                likes=note.get("likes") if note.get("likes") is not None else "",
                collects=note.get("collects") if note.get("collects") is not None else "",
                comments=note.get("comments") if note.get("comments") is not None else "",
            )
        )
    lines.append("")
    warnings = payload.get("warnings") or []
    if warnings:
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {item}" for item in warnings)
        lines.append("")
    return "\n".join(lines)


def _write_profile_files(
    profile: dict[str, Any],
    notes: list[dict[str, Any]],
    warnings: list[str],
) -> tuple[Path, Path, Path, dict[str, Any]]:
    profile_id = _safe_note_id(profile.get("profile_id") or profile.get("profile_url") or "profile")
    profile_dir = _xhs_profile_cache_root() / profile_id
    profile_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "platform": "xiaohongshu",
        "profile": profile,
        "notes": notes,
        "note_count": len(notes),
        "warnings": warnings,
        "extracted_at": now,
    }
    profile_json_path = profile_dir / "profile.json"
    profile_md_path = profile_dir / "profile.md"
    profile_json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    profile_md_path.write_text(_render_profile_markdown(payload), encoding="utf-8")
    return profile_dir, profile_json_path, profile_md_path, payload


def _yaml_string(value: Any) -> str:
    text = str(value or "")
    return json.dumps(text, ensure_ascii=False)


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if value is None:
        return '""'
    return _yaml_string(value)


def _render_frontmatter(data: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in data.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {_yaml_scalar(item)}")
        else:
            lines.append(f"{key}: {_yaml_scalar(value)}")
    lines.append("---")
    return "\n".join(lines)


def _write_text_if_missing(path: Path, content: str, *, overwrite: bool = False) -> bool:
    if path.exists() and not overwrite:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def _append_wiki_log(wiki_path: Path, line: str) -> None:
    log_path = wiki_path / "log.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(line.rstrip() + "\n")


def _open_wiki_in_obsidian(wiki_path: Path, *, target_path: str = "index.md") -> dict[str, Any]:
    wiki_path = wiki_path.resolve()
    target = (wiki_path / (target_path or "index.md")).resolve()
    status = _obsidian_status(wiki_path)
    result: dict[str, Any] = {
        "success": False,
        "opened": False,
        "target_path": str(target),
        "obsidian": status,
        "method": "",
        "stdout": "",
        "stderr": "",
    }
    if not target.exists():
        result["stderr"] = f"target file does not exist: {target}"
        return result
    if not status["cli_available"]:
        result["stderr"] = "obsidian command not found"
        return result

    vault_path_text = status.get("active_vault_path") or ""
    if status.get("inside_active_vault") and vault_path_text:
        vault_path = Path(vault_path_text)
        relative_target = target.relative_to(vault_path).as_posix()
        ok, stdout, stderr = _run_obsidian_cli(["open", f"path={relative_target}"], timeout=8.0)
        result.update(
            {
                "success": ok,
                "opened": ok,
                "method": "obsidian-cli:open",
                "stdout": stdout,
                "stderr": stderr,
            }
        )
        return result

    uri = f"obsidian://open?path={quote(str(target))}"
    try:
        proc = subprocess.run(
            ["open", uri],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=8.0,
            check=False,
        )
        ok = proc.returncode == 0
        result.update(
            {
                "success": ok,
                "opened": ok,
                "method": "obsidian-uri:open-path",
                "stdout": proc.stdout.strip(),
                "stderr": proc.stderr.strip(),
            }
        )
    except Exception as exc:
        result["stderr"] = str(exc)
    return result


def _init_lufei_wiki(wiki_path: Path, *, overwrite: bool = False) -> dict[str, Any]:
    directories = [
        "raw/xhs/notes",
        "raw/xhs/profile",
        "raw/courses",
        "raw/consulting",
        "raw/tencent-meetings",
        "raw/wechat-voice",
        "raw/assets",
        "entities",
        "concepts",
        "comparisons",
        "queries/script-briefs",
        "_derived",
    ]
    for relative in directories:
        (wiki_path / relative).mkdir(parents=True, exist_ok=True)
    now_date = datetime.now(timezone.utc).date().isoformat()
    written: list[str] = []

    templates = {
        "SCHEMA.md": f"""# 路飞设计沉思录 llm-wiki Schema

- created: {now_date}
- purpose: 小红书 AI 运营、课程/咨询知识沉淀、逐字稿生成上下文

## Rules

1. `raw/` 保存原始材料和抽取结果，必须带 source_url / ingested / sha256。
2. `concepts/` 只保存跨多源反复出现，或单源核心位置非常明确的稳定模式。
3. `queries/` 保存每周选题、逐字稿 brief、A/B 测试上下文。
4. 所有结论型页面必须引用 raw 来源路径，避免无证据生成。
5. `persona.md` 是路飞表达规则的单一来源，自动工具不得静默改写。
""",
        "index.md": """# 路飞设计沉思录 Wiki

## Core

- [[entities/lufei]]
- [[concepts/immersive-interview-format]]
- [[comparisons/high-vs-low-performing-notes]]
- [[queries/next-topic-recommendations]]

## Raw Sources

- `raw/xhs/notes/`
- `raw/courses/`
- `raw/consulting/`
- `raw/tencent-meetings/`
- `raw/wechat-voice/`
""",
        "log.md": f"# Wiki Log\n\n- {now_date}: initialized 路飞设计沉思录 llm-wiki skeleton.\n",
        "persona.md": """# 路飞设计沉思录 Persona

## 账号定位

- 设计求职 / UIUX / 作品集 / 大厂面试 / Web Coding 课程
- 专业、直接、像真实面试官，不走泛泛鸡汤
- 核心优势：能把面试官视角、作品集判断、真实追问讲清楚

## 语气

- 直接指出问题，但不羞辱用户
- 用具体公司、岗位、场景、追问制造代入感
- 优先给框架、判断标准、可执行动作

## 禁用表达

- 少用“姐妹”“宝宝”“普通人逆袭”等泛情绪话术
- 不输出无法被路飞专业判断支撑的空泛结论
- 不伪造上岸、offer、数据和用户反馈
""",
        "entities/lufei.md": f"""{_render_frontmatter({
            "title": "路飞设计沉思录",
            "created": now_date,
            "updated": now_date,
            "type": "entity",
            "tags": ["xhs", "creator", "uiux", "career"],
            "sources": [],
        })}
# 路飞设计沉思录

路飞设计沉思录是一个围绕 UI/UX 设计求职、作品集、模拟面试和 Web Coding 课程的内容账号。

待补充：履历、服务 SKU、课程结构、咨询交付流程。
""",
        "concepts/immersive-interview-format.md": f"""{_render_frontmatter({
            "title": "沉浸式模拟面试格式",
            "created": now_date,
            "updated": now_date,
            "type": "concept",
            "tags": ["xhs", "interview", "script"],
            "sources": [],
            "confidence": "draft",
        })}
# 沉浸式模拟面试格式

占位页。只有当至少两条小红书原始笔记或一条核心爆款样本完成入库后，才沉淀稳定公式。
""",
        "comparisons/high-vs-low-performing-notes.md": f"""{_render_frontmatter({
            "title": "高低表现笔记对照",
            "created": now_date,
            "updated": now_date,
            "type": "comparison",
            "tags": ["xhs", "review"],
            "sources": [],
            "confidence": "draft",
        })}
# 高低表现笔记对照

占位页。用于比较沉浸式模拟面试、观点帖、服务广告、图文帖的表现差异。
""",
        "queries/next-topic-recommendations.md": f"""{_render_frontmatter({
            "title": "下一批选题建议",
            "created": now_date,
            "updated": now_date,
            "type": "query",
            "tags": ["xhs", "topics"],
            "sources": [],
        })}
# 下一批选题建议

待由 `xhs-topic-selection` 基于 wiki context 生成。
""",
    }

    for relative, content in templates.items():
        if _write_text_if_missing(wiki_path / relative, content, overwrite=overwrite):
            written.append(str(wiki_path / relative))

    return {
        "success": True,
        "wiki_path": str(wiki_path),
        "obsidian": _obsidian_status(wiki_path),
        "created_directories": [str(wiki_path / item) for item in directories],
        "written_files": written,
    }


def _normalise_wiki_relative(path: Path, wiki_path: Path) -> str:
    with contextlib.suppress(ValueError):
        return path.relative_to(wiki_path).as_posix()
    return path.as_posix()


def _render_wiki_raw_note_markdown(payload: dict[str, Any], *, account: str, content_sha256: str) -> str:
    now = datetime.now(timezone.utc).isoformat()
    note_id = payload.get("note_id", "")
    frontmatter = _render_frontmatter(
        {
            "title": payload.get("title") or f"小红书笔记 {note_id}",
            "created": now[:10],
            "updated": now[:10],
            "type": "raw",
            "platform": "xiaohongshu",
            "account": account,
            "note_id": note_id,
            "note_type": payload.get("note_type", ""),
            "source_url": payload.get("source_url", ""),
            "resolved_url": payload.get("resolved_url", ""),
            "ingested": now,
            "sha256": content_sha256,
            "likes": payload.get("likes"),
            "collects": payload.get("collects"),
            "comments": payload.get("comments"),
            "tags": ["xhs", "raw-note"],
            "sources": [payload.get("source_url", "")] if payload.get("source_url") else [],
        }
    )
    transcript = payload.get("transcript") or {}
    comment_threads = payload.get("comment_threads") or {}
    lines = [
        frontmatter,
        f"# {payload.get('title') or note_id}",
        "",
        "## Body",
        "",
        payload.get("content") or "_No body text extracted._",
        "",
        "## Transcript",
        "",
        transcript.get("text") or "_No transcript extracted._",
        "",
        "## Interaction",
        "",
        f"- likes: {payload.get('likes') if payload.get('likes') is not None else ''}",
        f"- collects: {payload.get('collects') if payload.get('collects') is not None else ''}",
        f"- comments: {payload.get('comments') if payload.get('comments') is not None else ''}",
        f"- shares: {payload.get('shares') if payload.get('shares') is not None else ''}",
        "",
        "## Comment Sample",
        "",
    ]
    for comment in (comment_threads.get("items") or [])[:20]:
        author = (comment.get("author") or {}).get("nickname") or "anonymous"
        lines.append(f"- {author}: {comment.get('text', '')}")
    if not comment_threads.get("items"):
        lines.append("_No comments extracted._")
    lines.append("")
    lines.extend(
        [
            "## Extraction Files",
            "",
            "- note.json: `note.json`",
            "- transcript: `transcript.md`",
            "- comments: `comments.md`",
            "",
        ]
    )
    return "\n".join(lines)


def _render_wiki_transcript_markdown(payload: dict[str, Any]) -> str:
    transcript = payload.get("transcript") or {}
    return "\n".join(
        [
            f"# Transcript - {payload.get('title') or payload.get('note_id', '')}",
            "",
            f"- status: {transcript.get('status', '')}",
            f"- provider: {transcript.get('provider', '')}",
            f"- language: {transcript.get('language', '')}",
            f"- coverage_ratio: {transcript.get('coverage_ratio') or ''}",
            f"- coverage_status: {transcript.get('coverage_status', '')}",
            "",
            transcript.get("text") or "_No transcript extracted._",
            "",
        ]
    )


def _render_wiki_comments_markdown(payload: dict[str, Any]) -> str:
    comment_threads = payload.get("comment_threads") or {}
    lines = [
        f"# Comments - {payload.get('title') or payload.get('note_id', '')}",
        "",
        f"- status: {comment_threads.get('status', '')}",
        f"- source: {comment_threads.get('source', '')}",
        f"- count: {comment_threads.get('count') or 0}",
        f"- total_count: {comment_threads.get('total_count') if comment_threads.get('total_count') is not None else ''}",
        "",
    ]
    for comment in comment_threads.get("items") or []:
        author = (comment.get("author") or {}).get("nickname") or "anonymous"
        lines.extend(
            [
                f"## {comment.get('id') or author}",
                "",
                f"- author: {author}",
                f"- like_count: {comment.get('like_count') if comment.get('like_count') is not None else ''}",
                f"- time: {comment.get('time', '')}",
                "",
                comment.get("text", ""),
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
    if not comment_threads.get("items"):
        lines.append("_No comments extracted._")
        lines.append("")
    return "\n".join(lines)


def _render_wiki_profile_raw_markdown(payload: dict[str, Any], *, account: str, content_sha256: str) -> str:
    now = datetime.now(timezone.utc).isoformat()
    profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
    notes = payload.get("notes") if isinstance(payload.get("notes"), list) else []
    profile_id = profile.get("profile_id") or ""
    frontmatter = _render_frontmatter(
        {
            "title": profile.get("nickname") or account or profile_id or "小红书账号清单",
            "created": now[:10],
            "updated": now[:10],
            "type": "raw-profile",
            "platform": "xiaohongshu",
            "account": account,
            "profile_id": profile_id,
            "source_url": profile.get("profile_url", ""),
            "ingested": now,
            "sha256": content_sha256,
            "note_count": len(notes),
            "tags": ["xhs", "raw-profile", "creator-ops"],
            "sources": [profile.get("profile_url", "")] if profile.get("profile_url") else [],
        }
    )
    lines = [
        frontmatter,
        f"# {profile.get('nickname') or account or profile_id or '小红书账号清单'}",
        "",
        "## Profile",
        "",
        f"- profile_id: {profile_id}",
        f"- profile_url: {profile.get('profile_url', '')}",
        f"- description: {profile.get('description', '')}",
        f"- extracted_at: {payload.get('extracted_at', '')}",
        f"- note_count: {len(notes)}",
        "",
        "## Notes",
        "",
        "| # | note_id | title | type | likes | collects | comments | source |",
        "|---:|---|---|---|---:|---:|---:|---|",
    ]
    for index, note in enumerate(notes, start=1):
        lines.append(
            "| {index} | [{note_id}]({url}) | {title} | {note_type} | {likes} | {collects} | {comments} | {source} |".format(
                index=index,
                note_id=note.get("note_id", ""),
                url=note.get("url", ""),
                title=(note.get("title") or "").replace("|", "\\|"),
                note_type=note.get("note_type", ""),
                likes=note.get("likes") if note.get("likes") is not None else "",
                collects=note.get("collects") if note.get("collects") is not None else "",
                comments=note.get("comments") if note.get("comments") is not None else "",
                source=(note.get("source") or "").replace("|", "\\|"),
            )
        )
    lines.append("")
    warnings = payload.get("warnings") or []
    if warnings:
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {item}" for item in warnings)
        lines.append("")
    return "\n".join(lines)


def _ingest_note_to_wiki(
    note_json_path: Path,
    *,
    wiki_path: Path,
    note_md_path: Path | None = None,
    account: str = "路飞设计沉思录",
) -> dict[str, Any]:
    if not note_json_path.exists():
        raise FileNotFoundError(f"note_json_path does not exist: {note_json_path}")
    _init_lufei_wiki(wiki_path, overwrite=False)
    payload = json.loads(note_json_path.read_text(encoding="utf-8"))
    note_id = _safe_note_id(payload.get("note_id") or note_json_path.parent.name)
    note_dir = wiki_path / "raw" / "xhs" / "notes" / note_id
    note_dir.mkdir(parents=True, exist_ok=True)
    content_sha256 = _json_hash(
        {
            "title": payload.get("title"),
            "content": payload.get("content"),
            "transcript": (payload.get("transcript") or {}).get("text"),
            "comments": payload.get("comment_threads"),
            "stats": payload.get("stats"),
        }
    )

    target_json = note_dir / "note.json"
    target_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    target_md = note_dir / "note.md"
    target_md.write_text(
        _render_wiki_raw_note_markdown(payload, account=account, content_sha256=content_sha256),
        encoding="utf-8",
    )
    target_transcript = note_dir / "transcript.md"
    target_transcript.write_text(_render_wiki_transcript_markdown(payload), encoding="utf-8")
    target_comments = note_dir / "comments.md"
    target_comments.write_text(_render_wiki_comments_markdown(payload), encoding="utf-8")

    copied_note_md = ""
    if note_md_path and note_md_path.exists():
        copied = note_dir / "extracted_note.md"
        shutil.copyfile(note_md_path, copied)
        copied_note_md = str(copied)

    _append_wiki_log(
        wiki_path,
        f"- {datetime.now(timezone.utc).isoformat()}: ingested xhs note {note_id} -> raw/xhs/notes/{note_id}/",
    )
    return {
        "success": True,
        "wiki_path": str(wiki_path),
        "note_id": note_id,
        "sha256": content_sha256,
        "raw_note_dir": str(note_dir),
        "raw_note_md_path": str(target_md),
        "raw_note_json_path": str(target_json),
        "transcript_path": str(target_transcript),
        "comments_path": str(target_comments),
        "copied_extracted_note_md_path": copied_note_md,
    }


def _profile_note_artifacts_for_wiki(
    profile_json_path: Path,
    payload: dict[str, Any],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    artifacts: list[dict[str, str]] = []
    missing: list[dict[str, str]] = []
    seen: set[str] = set()

    def add_artifact(note_id: str, note_json_path_raw: str | Path, note_md_path_raw: str | Path | None = None, *, source: str) -> None:
        safe_note_id = _safe_note_id(note_id)
        if not safe_note_id or safe_note_id in seen:
            return
        seen.add(safe_note_id)
        note_json = Path(note_json_path_raw).expanduser()
        note_md = Path(note_md_path_raw).expanduser() if note_md_path_raw else note_json.parent / "note.md"
        record = {
            "note_id": safe_note_id,
            "note_json_path": str(note_json),
            "note_md_path": str(note_md),
            "source": source,
        }
        if note_json.exists():
            artifacts.append(record)
        else:
            missing.append(record)

    report_path = profile_json_path.parent / "all_notes_extraction_report.json"
    if report_path.exists():
        with contextlib.suppress(Exception):
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report_notes = report.get("notes") if isinstance(report, dict) else []
            if isinstance(report_notes, list):
                for item in report_notes:
                    if not isinstance(item, dict):
                        continue
                    note_id = _clean_text(item.get("note_id"))
                    note_json_path = _clean_text(item.get("note_json_path"))
                    if not note_id or not note_json_path:
                        continue
                    add_artifact(note_id, note_json_path, item.get("note_md_path"), source="all_notes_extraction_report")

    profile_notes = payload.get("notes") if isinstance(payload.get("notes"), list) else []
    cache_root = _xhs_cache_root()
    if profile_json_path.parent.parent.name == "profiles":
        cache_root = profile_json_path.parent.parent.parent
    for item in profile_notes:
        if not isinstance(item, dict):
            continue
        note_id = _clean_text(item.get("note_id"))
        if not note_id:
            continue
        add_artifact(
            note_id,
            cache_root / _safe_note_id(note_id) / "note.json",
            cache_root / _safe_note_id(note_id) / "note.md",
            source="profile_notes",
        )

    return artifacts, missing


def _ingest_account_to_wiki(
    profile_json_path: Path,
    *,
    wiki_path: Path,
    profile_md_path: Path | None = None,
    account: str = "路飞设计沉思录",
) -> dict[str, Any]:
    if not profile_json_path.exists():
        raise FileNotFoundError(f"profile_json_path does not exist: {profile_json_path}")
    _init_lufei_wiki(wiki_path, overwrite=False)
    payload = json.loads(profile_json_path.read_text(encoding="utf-8"))
    profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
    profile_id = _safe_note_id(profile.get("profile_id") or profile_json_path.parent.name or account)
    profile_dir = wiki_path / "raw" / "xhs" / "profile" / profile_id
    profile_dir.mkdir(parents=True, exist_ok=True)
    content_sha256 = _json_hash(
        {
            "profile": profile,
            "notes": payload.get("notes"),
            "note_count": payload.get("note_count"),
        }
    )

    target_json = profile_dir / "profile.json"
    target_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    target_md = profile_dir / "profile.md"
    target_md.write_text(
        _render_wiki_profile_raw_markdown(payload, account=account, content_sha256=content_sha256),
        encoding="utf-8",
    )

    copied_profile_md = ""
    if profile_md_path and profile_md_path.exists():
        copied = profile_dir / "extracted_profile.md"
        shutil.copyfile(profile_md_path, copied)
        copied_profile_md = str(copied)

    copied_reports: list[str] = []
    for report_name in ("all_notes_extraction_report.json", "all_notes_extraction_report.md", "all_notes_extract_summary.json"):
        report_path = profile_json_path.parent / report_name
        if report_path.exists():
            target_report = profile_dir / report_name
            shutil.copyfile(report_path, target_report)
            copied_reports.append(str(target_report))

    note_artifacts, missing_note_artifacts = _profile_note_artifacts_for_wiki(profile_json_path, payload)
    ingested_notes: list[dict[str, Any]] = []
    note_ingest_errors: list[dict[str, str]] = []
    for artifact in note_artifacts:
        try:
            ingested_notes.append(
                _ingest_note_to_wiki(
                    Path(artifact["note_json_path"]),
                    wiki_path=wiki_path,
                    note_md_path=Path(artifact["note_md_path"]),
                    account=account,
                )
            )
        except Exception as exc:
            note_ingest_errors.append(
                {
                    "note_id": artifact.get("note_id", ""),
                    "note_json_path": artifact.get("note_json_path", ""),
                    "error": str(exc),
                }
            )

    inventory_path = wiki_path / "queries" / "account-note-inventory.md"
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    inventory_path.write_text(
        "\n".join(
            [
                _render_frontmatter(
                    {
                        "title": "账号笔记清单",
                        "created": datetime.now(timezone.utc).date().isoformat(),
                        "updated": datetime.now(timezone.utc).date().isoformat(),
                        "type": "query",
                        "tags": ["xhs", "profile", "inventory"],
                        "sources": [_normalise_wiki_relative(target_md, wiki_path)],
                    }
                ),
                "# 账号笔记清单",
                "",
                f"来源：^[{_normalise_wiki_relative(target_md, wiki_path)}]",
                "",
                _render_profile_markdown(payload),
                "",
            ]
        ),
        encoding="utf-8",
    )

    _append_wiki_log(
        wiki_path,
        f"- {datetime.now(timezone.utc).isoformat()}: ingested xhs profile {profile_id} -> raw/xhs/profile/{profile_id}/; notes={len(ingested_notes)} missing={len(missing_note_artifacts)} errors={len(note_ingest_errors)}",
    )
    manifest = _build_wiki_manifest(wiki_path)
    return {
        "success": True,
        "wiki_path": str(wiki_path),
        "profile_id": profile_id,
        "sha256": content_sha256,
        "raw_profile_dir": str(profile_dir),
        "raw_profile_md_path": str(target_md),
        "raw_profile_json_path": str(target_json),
        "inventory_path": str(inventory_path),
        "copied_extracted_profile_md_path": copied_profile_md,
        "copied_report_paths": copied_reports,
        "ingested_notes_count": len(ingested_notes),
        "ingested_note_dirs": [item.get("raw_note_dir", "") for item in ingested_notes],
        "missing_note_artifacts_count": len(missing_note_artifacts),
        "missing_note_artifacts": missing_note_artifacts,
        "note_ingest_error_count": len(note_ingest_errors),
        "note_ingest_errors": note_ingest_errors,
        "manifest_path": str(wiki_path / "_derived" / "manifest.json"),
        "manifest_file_count": manifest.get("file_count"),
        "obsidian": _obsidian_status(wiki_path),
    }


def _parse_frontmatter(markdown: str) -> dict[str, Any]:
    if not markdown.startswith("---\n"):
        return {}
    end = markdown.find("\n---", 4)
    if end == -1:
        return {}
    frontmatter = markdown[4:end].strip()
    data: dict[str, Any] = {}
    current_key = ""
    for line in frontmatter.splitlines():
        if not line.strip():
            continue
        if line.startswith("  - ") and current_key:
            data.setdefault(current_key, []).append(line[4:].strip().strip('"'))
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        current_key = key.strip()
        value = value.strip()
        if not value:
            data[current_key] = []
        else:
            data[current_key] = value.strip('"')
    return data


def _build_wiki_manifest(wiki_path: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for path in sorted(wiki_path.rglob("*.md")):
        if any(part.startswith(".") for part in path.relative_to(wiki_path).parts):
            continue
        text = path.read_text(encoding="utf-8")
        meta = _parse_frontmatter(text)
        title_match = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
        rel = _normalise_wiki_relative(path, wiki_path)
        files.append(
            {
                "path": rel,
                "title": meta.get("title") or (title_match.group(1).strip() if title_match else path.stem),
                "type": meta.get("type") or rel.split("/", 1)[0],
                "tags": meta.get("tags") or [],
                "sources": meta.get("sources") or [],
                "sha256": _markdown_hash(text),
                "chars": len(text),
            }
        )
    manifest = {
        "schema_version": 1,
        "wiki_path": str(wiki_path),
        "obsidian": _obsidian_status(wiki_path),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "file_count": len(files),
        "files": files,
    }
    derived = wiki_path / "_derived"
    derived.mkdir(parents=True, exist_ok=True)
    (derived / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _query_wiki_context(
    wiki_path: Path,
    query: str,
    *,
    max_files: int = 8,
    max_chars: int = 12000,
) -> dict[str, Any]:
    query = _clean_text(query)
    if not query:
        raise ValueError("query is required")
    max_files = max(1, min(int(max_files or 8), 30))
    max_chars = max(1000, min(int(max_chars or 12000), 50000))
    terms = [item for item in re.split(r"[\s,，/|]+", query) if item]
    if not terms:
        terms = [query]
    candidates: list[tuple[int, Path, str, dict[str, Any]]] = []
    for path in sorted(wiki_path.rglob("*.md")):
        rel = _normalise_wiki_relative(path, wiki_path)
        if rel.startswith("_derived/"):
            continue
        text = path.read_text(encoding="utf-8")
        meta = _parse_frontmatter(text)
        haystack = f"{rel}\n{meta.get('title', '')}\n{text}".lower()
        score = 0
        for term in terms:
            term_l = term.lower()
            if term_l in haystack:
                score += haystack.count(term_l) * (5 if term_l in str(meta.get("title", "")).lower() else 1)
        if score:
            candidates.append((score, path, text, meta))
    candidates.sort(key=lambda item: item[0], reverse=True)
    selected = candidates[:max_files]
    chunks: list[str] = []
    used_chars = 0
    matches: list[dict[str, Any]] = []
    for score, path, text, meta in selected:
        rel = _normalise_wiki_relative(path, wiki_path)
        remaining = max_chars - used_chars
        if remaining <= 0:
            break
        excerpt = text[:remaining]
        chunks.append(f"<!-- source: {rel}; score: {score} -->\n{excerpt}")
        used_chars += len(excerpt)
        matches.append(
            {
                "path": rel,
                "title": meta.get("title") or path.stem,
                "type": meta.get("type") or rel.split("/", 1)[0],
                "score": score,
                "chars": len(text),
            }
        )
    return {
        "success": True,
        "wiki_path": str(wiki_path),
        "query": query,
        "matches": matches,
        "context": "\n\n---\n\n".join(chunks),
        "context_chars": used_chars,
    }


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


async def extract_xhs_profile_notes(
    url_or_text: str,
    *,
    max_notes: int = 500,
    scroll_rounds: int = 100,
    prefer_cdp: bool = True,
    cdp_auto_navigate: bool = True,
) -> dict[str, Any]:
    url = _extract_xhs_url(url_or_text)
    if not url or not _is_xhs_profile_url(url):
        raise ValueError("No supported Xiaohongshu profile URL found in input.")
    max_notes = max(1, min(int(max_notes or 500), 500))
    scroll_rounds = max(0, min(int(scroll_rounds or 100), 100))

    warnings: list[str] = []
    fetch = await _fetch_page(url)
    parsed = _extract_profile_notes_from_html(fetch.html_text, fetch.final_url or url)
    profile = parsed.get("profile") or {}
    notes = parsed.get("notes") or []
    warnings.extend(fetch.warnings)

    if prefer_cdp:
        cdp_payload, cdp_warnings = await _fetch_profile_notes_from_browser_cdp(
            fetch.final_url or url,
            max_notes=max_notes,
            scroll_rounds=scroll_rounds,
            auto_navigate=cdp_auto_navigate,
        )
        cdp_profile = cdp_payload.get("profile") or {}
        cdp_notes = cdp_payload.get("notes") or []
        if cdp_profile:
            profile = {**profile, **{key: value for key, value in cdp_profile.items() if value}}
        if cdp_notes:
            notes = _merge_profile_note_records(notes + cdp_notes)
        warnings.extend(cdp_warnings)

    profile.setdefault("platform", "xiaohongshu")
    profile.setdefault("profile_id", _extract_profile_id_from_url(fetch.final_url or url) or "")
    profile.setdefault("profile_url", fetch.final_url or url)
    notes = _merge_profile_note_records(notes)[:max_notes]
    if not notes:
        warnings.append(
            "No profile note cards were extracted. Open the profile in a logged-in Browser/CDP page and retry if the public SSR page is sparse."
        )

    profile_dir, profile_json_path, profile_md_path, payload = _write_profile_files(profile, notes, warnings)
    return {
        "success": True,
        "profile_id": profile.get("profile_id", ""),
        "nickname": profile.get("nickname", ""),
        "note_count": len(notes),
        "notes": notes,
        "output_dir": str(profile_dir),
        "profile_json_path": str(profile_json_path),
        "profile_md_path": str(profile_md_path),
        "warnings": warnings,
        "markdown_preview": _render_profile_markdown(payload)[:2000],
    }


def init_lufei_wiki(
    *,
    wiki_path: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    return _init_lufei_wiki(_resolve_wiki_path(wiki_path), overwrite=overwrite)


def ingest_note_to_wiki(
    *,
    note_json_path: str,
    wiki_path: str | None = None,
    note_md_path: str | None = None,
    account: str = "路飞设计沉思录",
) -> dict[str, Any]:
    return _ingest_note_to_wiki(
        Path(note_json_path).expanduser(),
        wiki_path=_resolve_wiki_path(wiki_path),
        note_md_path=Path(note_md_path).expanduser() if note_md_path else None,
        account=account or "路飞设计沉思录",
    )


def ingest_account_to_wiki(
    *,
    profile_json_path: str,
    wiki_path: str | None = None,
    profile_md_path: str | None = None,
    account: str = "路飞设计沉思录",
) -> dict[str, Any]:
    return _ingest_account_to_wiki(
        Path(profile_json_path).expanduser(),
        wiki_path=_resolve_wiki_path(wiki_path),
        profile_md_path=Path(profile_md_path).expanduser() if profile_md_path else None,
        account=account or "路飞设计沉思录",
    )


def build_wiki_manifest(*, wiki_path: str | None = None) -> dict[str, Any]:
    path = _resolve_wiki_path(wiki_path)
    if not path.exists():
        raise FileNotFoundError(f"wiki_path does not exist: {path}")
    return _build_wiki_manifest(path)


def query_wiki_context(
    *,
    query: str,
    wiki_path: str | None = None,
    max_files: int = 8,
    max_chars: int = 12000,
) -> dict[str, Any]:
    path = _resolve_wiki_path(wiki_path)
    if not path.exists():
        raise FileNotFoundError(f"wiki_path does not exist: {path}")
    return _query_wiki_context(path, query, max_files=max_files, max_chars=max_chars)


def open_wiki_in_obsidian(
    *,
    wiki_path: str | None = None,
    target_path: str = "index.md",
) -> dict[str, Any]:
    path = _resolve_wiki_path(wiki_path)
    if not path.exists():
        raise FileNotFoundError(f"wiki_path does not exist: {path}")
    return _open_wiki_in_obsidian(path, target_path=target_path or "index.md")


def run_content_skill(
    *,
    skill: str,
    input_text: str = "",
    input_path: str | None = None,
    output_path: str | None = None,
    pipeline_path: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    pipeline_default = (
        pipeline_path
        or os.environ.get("HERMES_XHS_PIPELINE_PATH")
        or os.environ.get("XHS_PIPELINE_PATH")
        or "/Users/champion/Documents/develop/skills/xhs-content-pipeline"
    )
    pipeline = Path(pipeline_default).expanduser()
    runner = pipeline / "run_skill.py"
    if not runner.exists():
        raise FileNotFoundError(f"xhs-content-pipeline runner not found: {runner}")
    if input_path:
        actual_input = Path(input_path).expanduser()
        if not actual_input.exists():
            raise FileNotFoundError(f"input_path does not exist: {actual_input}")
        cleanup_input = None
    else:
        if not input_text:
            raise ValueError("Either input_text or input_path is required.")
        tmp_dir = _xhs_cache_root() / "_skill_inputs"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        actual_input = tmp_dir / f"{int(time.time() * 1000)}-{_safe_note_id(skill)}.md"
        actual_input.write_text(input_text, encoding="utf-8")
        cleanup_input = actual_input

    actual_output = Path(output_path).expanduser() if output_path else None
    cmd = [sys.executable, str(runner), skill, "-i", str(actual_input)]
    if actual_output:
        cmd.extend(["-o", str(actual_output)])
    if model:
        cmd.extend(["--model", model])
    proc = subprocess.run(
        cmd,
        cwd=str(pipeline),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if cleanup_input:
        with contextlib.suppress(OSError):
            cleanup_input.unlink()
    if proc.returncode != 0:
        raise RuntimeError(f"xhs-content-pipeline skill failed: {proc.stderr.strip() or proc.stdout.strip()}")
    content = actual_output.read_text(encoding="utf-8") if actual_output and actual_output.exists() else proc.stdout
    return {
        "success": True,
        "skill": skill,
        "pipeline_path": str(pipeline),
        "output_path": str(actual_output) if actual_output else "",
        "content": content,
        "stderr": proc.stderr,
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


async def xhs_extract_profile_notes_handler(args: dict[str, Any], **_: Any) -> str:
    try:
        result = await extract_xhs_profile_notes(
            args.get("url", ""),
            max_notes=int(args.get("max_notes") or 500),
            scroll_rounds=int(args.get("scroll_rounds") or 100),
            prefer_cdp=_bool_arg(args, "prefer_cdp", True),
            cdp_auto_navigate=_bool_arg(args, "cdp_auto_navigate", True),
        )
        return tool_result(result)
    except Exception as exc:
        logger.warning("xhs_extract_profile_notes failed: %s", exc, exc_info=True)
        return tool_error(str(exc), success=False)


async def xhs_init_lufei_wiki_handler(args: dict[str, Any], **_: Any) -> str:
    try:
        return tool_result(
            init_lufei_wiki(
                wiki_path=args.get("wiki_path") or None,
                overwrite=_bool_arg(args, "overwrite", False),
            )
        )
    except Exception as exc:
        logger.warning("xhs_init_lufei_wiki failed: %s", exc, exc_info=True)
        return tool_error(str(exc), success=False)


async def xhs_ingest_note_to_wiki_handler(args: dict[str, Any], **_: Any) -> str:
    try:
        return tool_result(
            ingest_note_to_wiki(
                note_json_path=args.get("note_json_path", ""),
                note_md_path=args.get("note_md_path") or None,
                wiki_path=args.get("wiki_path") or None,
                account=args.get("account") or "路飞设计沉思录",
            )
        )
    except Exception as exc:
        logger.warning("xhs_ingest_note_to_wiki failed: %s", exc, exc_info=True)
        return tool_error(str(exc), success=False)


async def xhs_ingest_account_to_wiki_handler(args: dict[str, Any], **_: Any) -> str:
    try:
        return tool_result(
            ingest_account_to_wiki(
                profile_json_path=args.get("profile_json_path", ""),
                profile_md_path=args.get("profile_md_path") or None,
                wiki_path=args.get("wiki_path") or None,
                account=args.get("account") or "路飞设计沉思录",
            )
        )
    except Exception as exc:
        logger.warning("xhs_ingest_account_to_wiki failed: %s", exc, exc_info=True)
        return tool_error(str(exc), success=False)


async def xhs_build_wiki_manifest_handler(args: dict[str, Any], **_: Any) -> str:
    try:
        return tool_result(build_wiki_manifest(wiki_path=args.get("wiki_path") or None))
    except Exception as exc:
        logger.warning("xhs_build_wiki_manifest failed: %s", exc, exc_info=True)
        return tool_error(str(exc), success=False)


async def xhs_query_wiki_context_handler(args: dict[str, Any], **_: Any) -> str:
    try:
        return tool_result(
            query_wiki_context(
                query=args.get("query", ""),
                wiki_path=args.get("wiki_path") or None,
                max_files=int(args.get("max_files") or 8),
                max_chars=int(args.get("max_chars") or 12000),
            )
        )
    except Exception as exc:
        logger.warning("xhs_query_wiki_context failed: %s", exc, exc_info=True)
        return tool_error(str(exc), success=False)


async def xhs_open_wiki_in_obsidian_handler(args: dict[str, Any], **_: Any) -> str:
    try:
        return tool_result(
            open_wiki_in_obsidian(
                wiki_path=args.get("wiki_path") or None,
                target_path=args.get("target_path") or "index.md",
            )
        )
    except Exception as exc:
        logger.warning("xhs_open_wiki_in_obsidian failed: %s", exc, exc_info=True)
        return tool_error(str(exc), success=False)


async def xhs_run_content_skill_handler(args: dict[str, Any], **_: Any) -> str:
    try:
        return tool_result(
            run_content_skill(
                skill=args.get("skill", ""),
                input_text=args.get("input_text") or "",
                input_path=args.get("input_path") or None,
                output_path=args.get("output_path") or None,
                pipeline_path=args.get("pipeline_path") or None,
                model=args.get("model") or None,
            )
        )
    except Exception as exc:
        logger.warning("xhs_run_content_skill failed: %s", exc, exc_info=True)
        return tool_error(str(exc), success=False)
