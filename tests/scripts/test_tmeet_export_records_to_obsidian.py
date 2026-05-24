from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "tmeet_export_records_to_obsidian.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("_tmeet_export_under_test", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_fixture(root: Path, name: str, data: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_export_records_to_obsidian_writes_raw_assets_and_manifest(tmp_path):
    module = load_module()
    fixtures = tmp_path / "fixtures"
    wiki = tmp_path / "wiki"

    write_fixture(
        fixtures,
        "meeting_list_ended.json",
        {
            "message": "ok",
            "data": {
                "meetings": [
                    {
                        "meeting_id": "m1",
                        "meeting_code": "123456",
                        "subject": "作品集辅导 - 张同学",
                        "start_time": "2026-05-22T10:00:00+08:00",
                    },
                    {
                        "meeting_id": "m2",
                        "meeting_code": "654321",
                        "subject": "Web Coding 课程答疑",
                        "start_time": "2026-05-23T20:00:00+08:00",
                    },
                ],
                "next_page_token": "",
            },
        },
    )
    write_fixture(
        fixtures,
        "record_list.json",
        {
            "message": "ok",
            "data": {
                "records": [
                    {
                        "meeting_id": "m1",
                        "meeting_record_id": "r1",
                        "record_start_time": "2026-05-22T10:00:00+08:00",
                        "record_files": [{"record_file_id": "f1"}],
                    },
                    {
                        "meeting_id": "m2",
                        "meeting_record_id": "r2",
                        "record_start_time": "2026-05-23T20:00:00+08:00",
                        "record_files": [{"record_file_id": "f2"}],
                    },
                ],
                "next_page_token": "",
            },
        },
    )
    for record_id in ("r1", "r2"):
        write_fixture(
            fixtures,
            f"record_address_{record_id}.json",
            {"message": "ok", "data": {"address": f"https://example.com/{record_id}"}},
        )
    write_fixture(
        fixtures,
        "transcript_paragraphs_f1.json",
        {
            "message": "ok",
            "data": {
                "paragraphs": [
                    {
                        "start_time": "00:00:01",
                        "speaker_name": "路飞",
                        "text": "这里作品集需要讲清楚项目价值。",
                    }
                ]
            },
        },
    )
    write_fixture(
        fixtures,
        "transcript_paragraphs_f2.json",
        {
            "message": "ok",
            "data": {
                "paragraphs": [
                    {
                        "start_time": "00:00:03",
                        "speaker_name": "路飞",
                        "text": "这节课先讲 Web Coding 的页面结构。",
                    }
                ]
            },
        },
    )
    write_fixture(
        fixtures,
        "smart_minutes_f1.json",
        {"message": "ok", "data": {"summary": "作品集辅导重点：项目价值表达。"}},
    )
    write_fixture(
        fixtures,
        "smart_minutes_f2.json",
        {"message": "ok", "data": {"summary": "课程重点：Web Coding 项目拆解。"}},
    )

    rc = module.main(
        [
            "--start",
            "2026-05-22",
            "--end",
            "2026-05-23",
            "--wiki-path",
            str(wiki),
            "--fixture-dir",
            str(fixtures),
        ]
    )

    assert rc == 0
    manifest = json.loads(
        (wiki / "raw" / "tencent-meetings" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["exported_count"] == 2
    assert manifest["category_counts"] == {"coaching": 1, "courses": 1, "unknown": 0}

    coaching_source = next(
        (wiki / item["source_md"])
        for item in manifest["records"]
        if item["category"] == "coaching"
    )
    course_source = next(
        (wiki / item["source_md"])
        for item in manifest["records"]
        if item["category"] == "courses"
    )

    assert coaching_source.exists()
    assert course_source.exists()
    assert "contains_student_info: true" in coaching_source.read_text(encoding="utf-8")
    assert "category: \"courses\"" in course_source.read_text(encoding="utf-8")
    assert (
        "这里作品集需要讲清楚项目价值"
        in (coaching_source.parent / "transcript.md").read_text(encoding="utf-8")
    )
    assert (wiki / "_derived" / "tencent-meetings-manifest.json").exists()


def test_classify_meeting_marks_ambiguous_titles_unknown():
    module = load_module()

    assert module.classify_meeting("作品集辅导 - 张同学")[0] == "coaching"
    assert module.classify_meeting("Web Coding 课程答疑")[0] == "courses"
    assert module.classify_meeting("课程学员一对一复盘")[0] == "unknown"
