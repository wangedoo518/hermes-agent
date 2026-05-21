"""Xiaohongshu extraction plugin.

The tool is intentionally narrow: it handles one user-submitted
Xiaohongshu image-text or video note link at a time and stores the extracted
artifact under the current Hermes tenant cache. It does not search,
bulk-crawl, publish, or automate account activity.
"""

from __future__ import annotations

from plugins.xiaohongshu.tools import (
    XHS_EXTRACT_NOTE_SCHEMA,
    xhs_extract_note_handler,
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
