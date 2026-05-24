"""Xiaohongshu extraction and creator-ops plugin.

The tools intentionally stay user-submitted and local-first: extract a single
note, inventory a creator profile page, and persist artifacts into a local
llm-wiki. They do not publish, comment, or automate account activity.
"""

from __future__ import annotations

from plugins.xiaohongshu.tools import (
    XHS_BUILD_WIKI_MANIFEST_SCHEMA,
    XHS_EXTRACT_NOTE_SCHEMA,
    XHS_EXTRACT_PROFILE_NOTES_SCHEMA,
    XHS_INGEST_ACCOUNT_TO_WIKI_SCHEMA,
    XHS_INGEST_NOTE_TO_WIKI_SCHEMA,
    XHS_INIT_LUFEI_WIKI_SCHEMA,
    XHS_OPEN_WIKI_IN_OBSIDIAN_SCHEMA,
    XHS_QUERY_WIKI_CONTEXT_SCHEMA,
    XHS_RUN_CONTENT_SKILL_SCHEMA,
    xhs_build_wiki_manifest_handler,
    xhs_extract_note_handler,
    xhs_extract_profile_notes_handler,
    xhs_ingest_account_to_wiki_handler,
    xhs_ingest_note_to_wiki_handler,
    xhs_init_lufei_wiki_handler,
    xhs_open_wiki_in_obsidian_handler,
    xhs_query_wiki_context_handler,
    xhs_run_content_skill_handler,
)


def register(ctx) -> None:
    """Register Xiaohongshu tools with Hermes."""
    ctx.register_tool(
        name="xhs_extract_note",
        toolset="xhs",
        schema=XHS_EXTRACT_NOTE_SCHEMA,
        handler=xhs_extract_note_handler,
        is_async=True,
        description=XHS_EXTRACT_NOTE_SCHEMA.get("description", ""),
    )
    ctx.register_tool(
        name="xhs_extract_profile_notes",
        toolset="xhs",
        schema=XHS_EXTRACT_PROFILE_NOTES_SCHEMA,
        handler=xhs_extract_profile_notes_handler,
        is_async=True,
        description=XHS_EXTRACT_PROFILE_NOTES_SCHEMA.get("description", ""),
    )
    ctx.register_tool(
        name="xhs_init_lufei_wiki",
        toolset="xhs",
        schema=XHS_INIT_LUFEI_WIKI_SCHEMA,
        handler=xhs_init_lufei_wiki_handler,
        is_async=True,
        description=XHS_INIT_LUFEI_WIKI_SCHEMA.get("description", ""),
    )
    ctx.register_tool(
        name="xhs_ingest_note_to_wiki",
        toolset="xhs",
        schema=XHS_INGEST_NOTE_TO_WIKI_SCHEMA,
        handler=xhs_ingest_note_to_wiki_handler,
        is_async=True,
        description=XHS_INGEST_NOTE_TO_WIKI_SCHEMA.get("description", ""),
    )
    ctx.register_tool(
        name="xhs_ingest_account_to_wiki",
        toolset="xhs",
        schema=XHS_INGEST_ACCOUNT_TO_WIKI_SCHEMA,
        handler=xhs_ingest_account_to_wiki_handler,
        is_async=True,
        description=XHS_INGEST_ACCOUNT_TO_WIKI_SCHEMA.get("description", ""),
    )
    ctx.register_tool(
        name="xhs_build_wiki_manifest",
        toolset="xhs",
        schema=XHS_BUILD_WIKI_MANIFEST_SCHEMA,
        handler=xhs_build_wiki_manifest_handler,
        is_async=True,
        description=XHS_BUILD_WIKI_MANIFEST_SCHEMA.get("description", ""),
    )
    ctx.register_tool(
        name="xhs_query_wiki_context",
        toolset="xhs",
        schema=XHS_QUERY_WIKI_CONTEXT_SCHEMA,
        handler=xhs_query_wiki_context_handler,
        is_async=True,
        description=XHS_QUERY_WIKI_CONTEXT_SCHEMA.get("description", ""),
    )
    ctx.register_tool(
        name="xhs_open_wiki_in_obsidian",
        toolset="xhs",
        schema=XHS_OPEN_WIKI_IN_OBSIDIAN_SCHEMA,
        handler=xhs_open_wiki_in_obsidian_handler,
        is_async=True,
        description=XHS_OPEN_WIKI_IN_OBSIDIAN_SCHEMA.get("description", ""),
    )
    ctx.register_tool(
        name="xhs_run_content_skill",
        toolset="xhs",
        schema=XHS_RUN_CONTENT_SKILL_SCHEMA,
        handler=xhs_run_content_skill_handler,
        is_async=True,
        description=XHS_RUN_CONTENT_SKILL_SCHEMA.get("description", ""),
    )
