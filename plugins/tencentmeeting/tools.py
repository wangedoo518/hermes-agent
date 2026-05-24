"""Agent-facing Tencent Meeting tools."""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any

from tools.registry import tool_error, tool_result

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
EXPORT_SCRIPT = REPO_ROOT / "scripts" / "tmeet_export_records_to_obsidian.py"
CDP_CAPTURE_SCRIPT = REPO_ROOT / "scripts" / "tmeet_capture_meeting_record_page_cdp.py"
CDP_DETAIL_CAPTURE_SCRIPT = REPO_ROOT / "scripts" / "tmeet_capture_record_details_cdp.py"
LUFEI_CRM_SCRIPT = REPO_ROOT / "scripts" / "lufei_build_crm_from_tmeet.py"
LUFEI_CRM_QUERY_SCRIPT = REPO_ROOT / "scripts" / "lufei_query_crm_context.py"

TMEET_EXPORT_RECORDS_TO_OBSIDIAN_SCHEMA: dict[str, Any] = {
    "name": "tmeet_export_records_to_obsidian",
    "description": (
        "Export Tencent Meeting records through the official tmeet CLI into "
        "a local Obsidian llm-wiki raw/tencent-meetings folder. It lists ended "
        "meetings and records, fetches record address metadata, transcript "
        "paragraphs and smart minutes, classifies meetings as coaching/courses/"
        "unknown by title, writes source.md/json artifacts, and builds a "
        "manifest for downstream Hermes skills."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "start": {
                "type": "string",
                "description": "Start date or ISO time, e.g. 2026-05-01.",
            },
            "end": {
                "type": "string",
                "description": "End date or ISO time, e.g. 2026-05-23.",
            },
            "wiki_path": {
                "type": "string",
                "description": (
                    "Obsidian llm-wiki root. Defaults to the local "
                    "lufei-xhs-wiki vault."
                ),
            },
            "tmeet_bin": {
                "type": "string",
                "description": "Optional explicit tmeet binary path.",
            },
            "tmeet_repo": {
                "type": "string",
                "description": (
                    "Optional tencentmeeting-cli source repo path used as a "
                    "go-run fallback when no tmeet binary is installed."
                ),
            },
            "dry_run": {
                "type": "boolean",
                "description": "Classify and build a manifest without writing files.",
                "default": False,
            },
        },
        "required": ["start", "end"],
        "additionalProperties": False,
    },
}

TMEET_CAPTURE_MEETING_RECORD_PAGE_CDP_SCHEMA: dict[str, Any] = {
    "name": "tmeet_capture_meeting_record_page_cdp",
    "description": (
        "Capture Tencent Meeting web record-center data from a logged-in local "
        "Chrome through CDP and write a raw Obsidian llm-wiki evidence bundle. "
        "Use this as a browser-route supplement when the official tmeet CLI is "
        "rate-limited or missing web-only record assets. The tool does not "
        "store cookies or request headers."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "cdp_url": {
                "type": "string",
                "description": "Chrome DevTools endpoint, default http://127.0.0.1:9222.",
            },
            "url": {
                "type": "string",
                "description": (
                    "Tencent Meeting record-center URL. Defaults to "
                    "https://meeting.tencent.com/user-center/meeting-record."
                ),
            },
            "wiki_path": {
                "type": "string",
                "description": (
                    "Obsidian llm-wiki root. Defaults to the local lufei-xhs-wiki vault."
                ),
            },
            "wait_ms": {
                "type": "integer",
                "description": "Milliseconds to wait after page load while network responses arrive.",
                "default": 15000,
            },
            "max_pages": {
                "type": "integer",
                "description": (
                    "Maximum record-list pages to visit after the first page. "
                    "Use 0 to visit all visible pagination pages."
                ),
                "default": 1,
            },
            "output_dir": {
                "type": "string",
                "description": "Optional explicit capture output directory.",
            },
        },
        "additionalProperties": False,
    },
}

TMEET_CAPTURE_RECORD_DETAILS_CDP_SCHEMA: dict[str, Any] = {
    "name": "tmeet_capture_record_details_cdp",
    "description": (
        "Open Tencent Meeting record detail pages from the latest CDP record "
        "list in a logged-in Chrome session, then save DOM-rendered smart "
        "minutes, transcript text, and response evidence into the Obsidian "
        "llm-wiki raw/tencent-meetings/cdp-details folder."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "cdp_url": {
                "type": "string",
                "description": "Chrome DevTools endpoint, default http://127.0.0.1:9222.",
            },
            "wiki_path": {
                "type": "string",
                "description": (
                    "Obsidian llm-wiki root. Defaults to the local lufei-xhs-wiki vault."
                ),
            },
            "record_list_path": {
                "type": "string",
                "description": "Optional explicit record list JSON path.",
            },
            "output_root": {
                "type": "string",
                "description": "Optional explicit detail output root directory.",
            },
            "offset": {
                "type": "integer",
                "description": "Start offset in the record list.",
                "default": 0,
            },
            "limit": {
                "type": "integer",
                "description": "Number of records to capture. Use 0 for all records.",
                "default": 1,
            },
            "wait_ms": {
                "type": "integer",
                "description": "Milliseconds to wait for each detail page to render.",
                "default": 10000,
            },
            "start": {
                "type": "string",
                "description": "Optional start date or ISO datetime for record start_time filtering.",
            },
            "end": {
                "type": "string",
                "description": "Optional end date or ISO datetime for record start_time filtering.",
            },
            "category": {
                "type": "string",
                "enum": ["coaching", "courses", "unknown"],
                "description": "Optional category filter from the CDP record list.",
            },
            "download_videos": {
                "type": "boolean",
                "description": (
                    "Download MP4 video sources exposed by the detail page into "
                    "each record's videos/ folder. Defaults to false because a "
                    "full 369-record archive can be around 100GB."
                ),
                "default": False,
            },
            "skip_existing_videos": {
                "type": "boolean",
                "description": "Skip a video when the target MP4 already exists and is non-empty.",
                "default": True,
            },
        },
        "additionalProperties": False,
    },
}

TMEET_BUILD_LUFEI_CRM_SCHEMA: dict[str, Any] = {
    "name": "tmeet_build_lufei_crm",
    "description": (
        "Build 路飞's Obsidian CRM layer from Tencent Meeting raw detail assets. "
        "It reads the latest tmeet_capture_record_details_cdp manifest, classifies "
        "student/coaching/course meetings, writes crm/students/*.md, "
        "crm/index.md, crm/meeting-assets.md, and _derived/lufei-crm-manifest.json."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "wiki_path": {
                "type": "string",
                "description": (
                    "Obsidian llm-wiki root. Defaults to the local lufei-xhs-wiki vault."
                ),
            },
            "detail_manifest": {
                "type": "string",
                "description": (
                    "Optional explicit detail_manifest.json path. Defaults to "
                    "_derived/tencent-meetings-cdp-details-latest.json."
                ),
            },
        },
        "additionalProperties": False,
    },
}

TMEET_QUERY_LUFEI_CRM_CONTEXT_SCHEMA: dict[str, Any] = {
    "name": "tmeet_query_lufei_crm_context",
    "description": (
        "Query 路飞's Obsidian CRM layer and assemble evidence-backed context "
        "from Tencent Meeting smart minutes/transcripts. Use this before "
        "student progress summaries, mock interview preparation, content ideas, "
        "or CRM follow-up answers."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural-language CRM query, e.g. 吴晓钰最近作品集进展.",
            },
            "student": {
                "type": "string",
                "description": "Optional explicit student name filter.",
            },
            "theme": {
                "type": "string",
                "description": "Optional comma-separated theme filter, e.g. portfolio,interview.",
            },
            "service_type": {
                "type": "string",
                "description": "Optional service filter, e.g. coaching or mock_interview.",
            },
            "wiki_path": {
                "type": "string",
                "description": (
                    "Obsidian llm-wiki root. Defaults to the local lufei-xhs-wiki vault."
                ),
            },
            "max_meetings": {
                "type": "integer",
                "description": "Maximum evidence meetings to return.",
                "default": 8,
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum context_markdown characters.",
                "default": 12000,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}


def _load_exporter():
    spec = importlib.util.spec_from_file_location("_tmeet_exporter", EXPORT_SCRIPT)
    if not spec or not spec.loader:
        raise RuntimeError(f"Cannot load exporter script: {EXPORT_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_cdp_capture():
    spec = importlib.util.spec_from_file_location("_tmeet_cdp_capture", CDP_CAPTURE_SCRIPT)
    if not spec or not spec.loader:
        raise RuntimeError(f"Cannot load CDP capture script: {CDP_CAPTURE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_cdp_detail_capture():
    spec = importlib.util.spec_from_file_location(
        "_tmeet_cdp_detail_capture", CDP_DETAIL_CAPTURE_SCRIPT
    )
    if not spec or not spec.loader:
        raise RuntimeError(f"Cannot load CDP detail capture script: {CDP_DETAIL_CAPTURE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_lufei_crm_builder():
    spec = importlib.util.spec_from_file_location("_lufei_crm_builder", LUFEI_CRM_SCRIPT)
    if not spec or not spec.loader:
        raise RuntimeError(f"Cannot load CRM builder script: {LUFEI_CRM_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_lufei_crm_query():
    spec = importlib.util.spec_from_file_location(
        "_lufei_crm_query", LUFEI_CRM_QUERY_SCRIPT
    )
    if not spec or not spec.loader:
        raise RuntimeError(f"Cannot load CRM query script: {LUFEI_CRM_QUERY_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def tmeet_export_records_to_obsidian_handler(args: dict[str, Any], **_: Any) -> str:
    try:
        module = _load_exporter()
        wiki_path = Path(args["wiki_path"]) if args.get("wiki_path") else module.DEFAULT_WIKI_PATH
        tmeet_repo = Path(args["tmeet_repo"]) if args.get("tmeet_repo") else module.DEFAULT_TMEET_REPO
        runner = module.TmeetRunner(
            tmeet_bin=args.get("tmeet_bin") or None,
            tmeet_repo=tmeet_repo,
        )
        manifest = await asyncio.to_thread(
            module.export_records,
            runner=runner,
            wiki_path=wiki_path,
            start=args["start"],
            end=args["end"],
            dry_run=bool(args.get("dry_run", False)),
        )
        raw_root = wiki_path / "raw" / "tencent-meetings"
        return tool_result(
            {
                "success": True,
                "wiki_path": str(wiki_path),
                "raw_root": str(raw_root),
                "manifest_path": str(raw_root / "manifest.json"),
                "derived_manifest_path": str(
                    wiki_path / "_derived" / "tencent-meetings-manifest.json"
                ),
                "exported_count": manifest.get("exported_count", 0),
                "category_counts": manifest.get("category_counts", {}),
                "warnings": manifest.get("warnings", []),
            }
        )
    except Exception as exc:
        logger.warning("tmeet_export_records_to_obsidian failed: %s", exc, exc_info=True)
        return tool_error(str(exc), success=False)


async def tmeet_capture_meeting_record_page_cdp_handler(args: dict[str, Any], **_: Any) -> str:
    try:
        module = _load_cdp_capture()
        wiki_path = Path(args["wiki_path"]) if args.get("wiki_path") else module.DEFAULT_WIKI_PATH
        output_dir = Path(args["output_dir"]) if args.get("output_dir") else None
        report = await asyncio.to_thread(
            module.capture_meeting_record_page,
            cdp_url=args.get("cdp_url") or module.DEFAULT_CDP_URL,
            record_url=args.get("url") or module.DEFAULT_RECORD_URL,
            wiki_path=wiki_path,
            wait_ms=int(args.get("wait_ms") or 15000),
            max_pages=int(args.get("max_pages") if args.get("max_pages") is not None else 1),
            output_dir=output_dir,
        )
        return tool_result(
            {
                "success": True,
                "wiki_path": str(wiki_path),
                "capture_root": report.get("capture_root"),
                "requires_web_login": report.get("requires_web_login", False),
                "response_count": report.get("response_count", 0),
                "json_response_count": report.get("json_response_count", 0),
                "record_list_page_count": report.get("record_list_page_count", 0),
                "record_list_total_count": report.get("record_list_total_count", 0),
                "record_list_exported_count": report.get("record_list_exported_count", 0),
                "warnings": report.get("warnings", []),
                "latest_manifest": str(
                    wiki_path / "_derived" / "tencent-meetings-cdp-latest.json"
                ),
            }
        )
    except Exception as exc:
        logger.warning(
            "tmeet_capture_meeting_record_page_cdp failed: %s", exc, exc_info=True
        )
        return tool_error(str(exc), success=False)


async def tmeet_capture_record_details_cdp_handler(args: dict[str, Any], **_: Any) -> str:
    try:
        module = _load_cdp_detail_capture()
        wiki_path = Path(args["wiki_path"]) if args.get("wiki_path") else module.DEFAULT_WIKI_PATH
        record_list_path = Path(args["record_list_path"]) if args.get("record_list_path") else None
        output_root = Path(args["output_root"]) if args.get("output_root") else None
        manifest = await asyncio.to_thread(
            module.capture_record_details,
            cdp_url=args.get("cdp_url") or module.DEFAULT_CDP_URL,
            wiki_path=wiki_path,
            record_list_path=record_list_path,
            output_root=output_root,
            offset=int(args.get("offset") or 0),
            limit=int(args.get("limit") if args.get("limit") is not None else 1),
            wait_ms=int(args.get("wait_ms") or 10000),
            start=args.get("start") or None,
            end=args.get("end") or None,
            category=args.get("category") or None,
            download_videos=bool(args.get("download_videos", False)),
            skip_existing_videos=bool(args.get("skip_existing_videos", True)),
        )
        return tool_result(
            {
                "success": True,
                "wiki_path": str(wiki_path),
                "output_root": manifest.get("output_root"),
                "filtered_record_count": manifest.get("filtered_record_count", 0),
                "attempted_count": manifest.get("attempted_count", 0),
                "exported_count": manifest.get("exported_count", 0),
                "timeline_item_count": manifest.get("timeline_item_count", 0),
                "video_source_count": manifest.get("video_source_count", 0),
                "video_downloaded_count": manifest.get("video_downloaded_count", 0),
                "warnings": manifest.get("warnings", []),
                "latest_manifest": str(
                    wiki_path / "_derived" / "tencent-meetings-cdp-details-latest.json"
                ),
            }
        )
    except Exception as exc:
        logger.warning("tmeet_capture_record_details_cdp failed: %s", exc, exc_info=True)
        return tool_error(str(exc), success=False)


async def tmeet_build_lufei_crm_handler(args: dict[str, Any], **_: Any) -> str:
    try:
        module = _load_lufei_crm_builder()
        wiki_path = Path(args["wiki_path"]) if args.get("wiki_path") else module.DEFAULT_WIKI_PATH
        detail_manifest = Path(args["detail_manifest"]) if args.get("detail_manifest") else None
        manifest = await asyncio.to_thread(
            module.build_crm_from_tmeet,
            wiki_path=wiki_path,
            detail_manifest=detail_manifest,
        )
        return tool_result(
            {
                "success": True,
                "wiki_path": manifest.get("wiki_path"),
                "crm_index_path": str(wiki_path / manifest.get("crm_index_path", "crm/index.md")),
                "manifest_path": str(
                    wiki_path / manifest.get("manifest_path", "_derived/lufei-crm-manifest.json")
                ),
                "meeting_count": manifest.get("meeting_count", 0),
                "student_count": manifest.get("student_count", 0),
                "course_count": manifest.get("course_count", 0),
                "classification_counts": manifest.get("classification_counts", {}),
                "missing_text_counts": manifest.get("missing_text_counts", {}),
            }
        )
    except Exception as exc:
        logger.warning("tmeet_build_lufei_crm failed: %s", exc, exc_info=True)
        return tool_error(str(exc), success=False)


async def tmeet_query_lufei_crm_context_handler(args: dict[str, Any], **_: Any) -> str:
    try:
        module = _load_lufei_crm_query()
        wiki_path = Path(args["wiki_path"]) if args.get("wiki_path") else module.DEFAULT_WIKI_PATH
        result = await asyncio.to_thread(
            module.query_crm_context,
            wiki_path=wiki_path,
            query=args.get("query") or "",
            student=args.get("student") or None,
            theme=args.get("theme") or None,
            service_type=args.get("service_type") or None,
            max_meetings=int(args.get("max_meetings") or 8),
            max_chars=int(args.get("max_chars") or 12000),
        )
        return tool_result(result)
    except Exception as exc:
        logger.warning("tmeet_query_lufei_crm_context failed: %s", exc, exc_info=True)
        return tool_error(str(exc), success=False)
