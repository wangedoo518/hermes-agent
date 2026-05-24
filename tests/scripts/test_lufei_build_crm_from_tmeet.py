from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "lufei_build_crm_from_tmeet.py"
QUERY_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "lufei_query_crm_context.py"


def load_module():
    spec = importlib.util.spec_from_file_location("lufei_build_crm_from_tmeet", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_query_module():
    spec = importlib.util.spec_from_file_location("lufei_query_crm_context", QUERY_SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_record(root: Path, *, title: str, record_id: str, category: str = "unknown") -> None:
    record_dir = root / f"2026-05-20-10-00-{title}-{record_id[:8]}"
    record_dir.mkdir(parents=True)
    record = {
        "record_id": record_id,
        "title": title,
        "category": category,
        "start_time_text": "2026-05-20 10:00",
        "end_time_text": "2026-05-20 11:00",
        "meeting_code": "123456789",
        "duration_ms": "3600000",
    }
    detail = {
        "success": True,
        "record": record,
        "output_dir": str(record_dir),
        "final_url": f"https://meeting.tencent.com/meeting-record/shares?id={record_id}",
        "smart_minutes_char_count": 120,
        "timeline_item_count": 3,
        "transcript_char_count": 240,
        "warnings": [],
    }
    (record_dir / "detail_report.json").write_text(
        json.dumps(detail, ensure_ascii=False), encoding="utf-8"
    )
    (record_dir / "source.md").write_text(
        f'---\ntitle: "{title}"\nsource_url: "https://example.com/{record_id}"\n---\n',
        encoding="utf-8",
    )
    (record_dir / "smart_minutes.md").write_text(
        f"# {title} - Smart Minutes\n\n会议总结\n\n本次会议讨论作品集、面试表达和AI Agent项目推进。",
        encoding="utf-8",
    )
    (record_dir / "timeline.md").write_text("# Timeline\n\n- 00:01 讨论作品集\n", encoding="utf-8")
    (record_dir / "timeline.json").write_text("[]", encoding="utf-8")
    (record_dir / "transcript.md").write_text(
        "# Transcript\n\n路飞：我们看一下作品集和面试表达。",
        encoding="utf-8",
    )


def test_build_crm_from_tmeet_fixture(tmp_path: Path) -> None:
    module = load_module()
    wiki = tmp_path / "wiki"
    root = wiki / "raw" / "tencent-meetings" / "cdp-details" / "fixture"
    root.mkdir(parents=True)

    records = [
        ("吴晓钰辅导", "rec-1", "coaching"),
        ("赵益笳 王怡华辅导", "rec-2", "coaching"),
        ("面试高分表达：高频真题拆解(上)", "rec-3", "unknown"),
        ("路飞预定的会议", "rec-4", "unknown"),
    ]
    reports = []
    for title, record_id, category in records:
        write_record(root, title=title, record_id=record_id, category=category)
        record_dir = root / f"2026-05-20-10-00-{title}-{record_id[:8]}"
        reports.append({"output_dir": str(record_dir), "record": {"title": title, "category": category}})
    detail_manifest = {
        "success": True,
        "output_root": str(root),
        "attempted_count": len(reports),
        "exported_count": len(reports),
        "reports": reports,
    }
    detail_manifest_path = root / "detail_manifest.json"
    detail_manifest_path.write_text(
        json.dumps(detail_manifest, ensure_ascii=False), encoding="utf-8"
    )

    manifest = module.build_crm_from_tmeet(
        wiki_path=wiki,
        detail_manifest=detail_manifest_path,
    )

    assert manifest["meeting_count"] == 4
    assert manifest["student_count"] == 3
    assert manifest["course_count"] == 1
    assert manifest["classification_counts"] == {"student": 2, "course": 1, "unknown": 1}
    assert (wiki / "crm" / "index.md").exists()
    assert (wiki / "crm" / "meeting-assets.md").exists()
    assert (wiki / "_derived" / "lufei-crm-manifest.json").exists()
    assert "吴晓钰" in manifest["students"]
    assert "赵益笳" in manifest["students"]
    assert "王怡华" in manifest["students"]

    query_module = load_query_module()
    context = query_module.query_crm_context(
        wiki_path=wiki,
        query="吴晓钰最近作品集进展",
        max_meetings=2,
        max_chars=4000,
    )
    assert context["success"] is True
    assert [student["name"] for student in context["student_matches"]] == ["吴晓钰"]
    assert context["returned_meeting_count"] == 1
    assert "吴晓钰辅导" in context["context_markdown"]


def test_title_classification_rules() -> None:
    module = load_module()

    assert module.infer_classification("吴晓钰辅导") == ("student", "coaching", ["吴晓钰"])
    assert module.infer_classification("赵益笳 王怡华辅导") == (
        "student",
        "coaching",
        ["赵益笳", "王怡华"],
    )
    assert module.infer_classification("Ruoling Xu 咨询") == (
        "student",
        "consultation",
        ["Ruoling Xu"],
    )
    assert module.infer_classification("面试高分表达：高频真题拆解(上)") == (
        "course",
        "course",
        [],
    )
    assert module.infer_classification("路飞预定的会议") == ("unknown", "unknown", [])
