"""Tencent Meeting asset export plugin."""

from __future__ import annotations

from plugins.tencentmeeting.tools import (
    TMEET_CAPTURE_MEETING_RECORD_PAGE_CDP_SCHEMA,
    TMEET_CAPTURE_RECORD_DETAILS_CDP_SCHEMA,
    TMEET_BUILD_LUFEI_CRM_SCHEMA,
    TMEET_EXPORT_RECORDS_TO_OBSIDIAN_SCHEMA,
    TMEET_QUERY_LUFEI_CRM_CONTEXT_SCHEMA,
    tmeet_build_lufei_crm_handler,
    tmeet_capture_meeting_record_page_cdp_handler,
    tmeet_capture_record_details_cdp_handler,
    tmeet_export_records_to_obsidian_handler,
    tmeet_query_lufei_crm_context_handler,
)


def register(ctx) -> None:
    """Register Tencent Meeting tools with Hermes."""
    ctx.register_tool(
        name="tmeet_export_records_to_obsidian",
        toolset="tmeet",
        schema=TMEET_EXPORT_RECORDS_TO_OBSIDIAN_SCHEMA,
        handler=tmeet_export_records_to_obsidian_handler,
        is_async=True,
        description=TMEET_EXPORT_RECORDS_TO_OBSIDIAN_SCHEMA.get("description", ""),
    )
    ctx.register_tool(
        name="tmeet_capture_meeting_record_page_cdp",
        toolset="tmeet",
        schema=TMEET_CAPTURE_MEETING_RECORD_PAGE_CDP_SCHEMA,
        handler=tmeet_capture_meeting_record_page_cdp_handler,
        is_async=True,
        description=TMEET_CAPTURE_MEETING_RECORD_PAGE_CDP_SCHEMA.get("description", ""),
    )
    ctx.register_tool(
        name="tmeet_capture_record_details_cdp",
        toolset="tmeet",
        schema=TMEET_CAPTURE_RECORD_DETAILS_CDP_SCHEMA,
        handler=tmeet_capture_record_details_cdp_handler,
        is_async=True,
        description=TMEET_CAPTURE_RECORD_DETAILS_CDP_SCHEMA.get("description", ""),
    )
    ctx.register_tool(
        name="tmeet_build_lufei_crm",
        toolset="tmeet",
        schema=TMEET_BUILD_LUFEI_CRM_SCHEMA,
        handler=tmeet_build_lufei_crm_handler,
        is_async=True,
        description=TMEET_BUILD_LUFEI_CRM_SCHEMA.get("description", ""),
    )
    ctx.register_tool(
        name="tmeet_query_lufei_crm_context",
        toolset="tmeet",
        schema=TMEET_QUERY_LUFEI_CRM_CONTEXT_SCHEMA,
        handler=tmeet_query_lufei_crm_context_handler,
        is_async=True,
        description=TMEET_QUERY_LUFEI_CRM_CONTEXT_SCHEMA.get("description", ""),
    )
