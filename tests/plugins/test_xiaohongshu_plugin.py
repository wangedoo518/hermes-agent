import asyncio
import json
import os
from pathlib import Path

import pytest

from plugins.xiaohongshu import register
from plugins.xiaohongshu import tools as xhs


@pytest.fixture(autouse=True)
def _disable_real_browser_extractor(monkeypatch):
    monkeypatch.setenv("HERMES_XHS_NOTE_EXTRACTOR", "ssr")


def test_extracts_xhs_url_and_note_id_from_share_text():
    note_id = "65b7b9d00000000001001234"
    text = (
        "复制这条小红书笔记 "
        f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token=abc，"
        "打开看看"
    )

    url = xhs._extract_xhs_url(text)

    assert url == f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token=abc"
    assert xhs._extract_note_id_from_url(url) == note_id


def test_rejects_non_xhs_url_from_share_text():
    assert xhs._extract_xhs_url("look https://example.com/explore/65b7b9d00000000001001234") is None


def test_extracts_metadata_from_html_with_meta_and_initial_state():
    html = """
    <html>
      <head>
        <meta property="og:title" content="秋型通勤穿搭 - 小红书">
        <meta property="og:description" content="低饱和外套和棕色半裙，适合早秋上班。">
        <meta property="og:image" content="https://sns-img-qc.xhscdn.com/meta-image">
      </head>
      <body>
        <script>
          window.__INITIAL_STATE__ = {
            "note": {
              "noteDetailMap": {
                "abc": {
                  "note": {
                    "displayTitle": "被 meta 覆盖的标题",
                    "desc": "更长的图文正文，用来验证 JSON 也能被提取出来。",
                    "interactInfo": {
                      "likedCount": "2800",
                      "collectedCount": "2700",
                      "commentCount": "143",
                      "shareCount": "428"
                    },
                    "comments": {
                      "list": [
                        {
                          "id": "c1",
                          "content": "求耳环店铺名，适合通勤吗？",
                          "likeCount": "8",
                          "createTime": "2026-05-21",
                          "ipLocation": "北京",
                          "userInfo": {"userId": "u1", "nickname": "Tame light 和光"},
                          "subComments": [
                            {
                              "id": "r1",
                              "content": "适合通勤，也适合小礼服。",
                              "userInfo": {"userId": "author1", "nickname": "Aria空"}
                            }
                          ]
                        }
                      ],
                      "cursor": "next-cursor",
                      "hasMore": true
                    },
                    "imageList": [
                      {"urlDefault": "https:\\/\\/sns-img-qc.xhscdn.com\\/image-a"},
                      {"urlPre": "https://sns-img-qc.xhscdn.com/image-b"}
                    ]
                  }
                }
              }
            }
          };
        </script>
      </body>
    </html>
    """

    metadata = xhs._extract_metadata_from_html(
        html,
        base_url="https://www.xiaohongshu.com/explore/65b7b9d00000000001001234",
    )

    assert metadata["title"] == "秋型通勤穿搭"
    assert metadata["content"] == "低饱和外套和棕色半裙，适合早秋上班。"
    assert metadata["images"] == [
        "https://sns-img-qc.xhscdn.com/meta-image",
        "https://sns-img-qc.xhscdn.com/image-a",
        "https://sns-img-qc.xhscdn.com/image-b",
    ]
    assert metadata["stats"]["status"] == "ok"
    assert metadata["stats"]["like_count"] == 2800
    assert metadata["stats"]["collect_count"] == 2700
    assert metadata["stats"]["comment_count"] == 143
    assert metadata["stats"]["share_count"] == 428
    assert metadata["comment_threads"]["status"] == "ok"
    assert metadata["comment_threads"]["count"] == 1
    assert metadata["comment_threads"]["has_more"] is True
    assert metadata["comment_threads"]["items"][0]["author"]["nickname"] == "Tame light 和光"
    assert metadata["comment_threads"]["items"][0]["like_count"] == 8
    assert metadata["comment_threads"]["items"][0]["replies"][0]["text"] == "适合通勤，也适合小礼服。"


def test_extracts_note_body_from_description_when_og_description_is_generic():
    html = """
    <html>
      <head>
        <meta name="description" content="五一后找工作的人请进～ 面试中有这5个信号的直接被pass。">
        <meta property="og:description" content="3 亿人的生活经验，都在小红书">
        <meta property="og:title" content="春招时面试官是怎么淘汰一个人的 - 小红书">
      </head>
    </html>
    """

    metadata = xhs._extract_metadata_from_html(
        html,
        base_url="https://www.xiaohongshu.com/explore/69eeef59000000000f03ac00",
    )

    assert metadata["title"] == "春招时面试官是怎么淘汰一个人的"
    assert metadata["content"] == "五一后找工作的人请进～ 面试中有这5个信号的直接被pass。"


def test_parses_xhs_comment_api_payload():
    payload = {
        "success": True,
        "data": {
            "cursor": "next-cursor",
            "has_more": True,
            "comments": [
                {
                    "id": "c1",
                    "content": "求耳环店铺名，适合通勤吗？",
                    "like_count": "12",
                    "create_time": "2026-05-21",
                    "ip_location": "北京",
                    "user_info": {"user_id": "u1", "nickname": "Tame light 和光"},
                    "sub_comments": [
                        {
                            "id": "r1",
                            "content": "适合通勤，也适合小礼服。",
                            "user_info": {"user_id": "author1", "nickname": "Aria空"},
                        }
                    ],
                }
            ],
        },
    }

    threads = xhs._parse_comment_api_payload(payload, source="api:sns/web/v2/comment/page")

    assert threads["status"] == "ok"
    assert threads["source"] == "api:sns/web/v2/comment/page"
    assert threads["count"] == 1
    assert threads["has_more"] is True
    assert threads["cursor"] == "next-cursor"
    assert threads["items"][0]["text"] == "求耳环店铺名，适合通勤吗？"
    assert threads["items"][0]["author"]["nickname"] == "Tame light 和光"
    assert threads["items"][0]["like_count"] == 12
    assert threads["items"][0]["replies"][0]["text"] == "适合通勤，也适合小礼服。"


def test_parses_browser_cdp_comment_dom_payload():
    threads = xhs._parse_comment_dom_payload(
        {
            "total_count": 143,
            "items": [
                {
                    "id": "comment_1",
                    "author": "onom",
                    "text": "这就是压力面吗？",
                    "time": "04-12北京",
                    "like_count": 33,
                    "replies": [
                        {
                            "id": "reply_1",
                            "author": "路飞设计沉思录",
                            "text": "一般面试官问的时候会表情更严肃",
                            "time": "04-12浙江",
                            "like_count": 4,
                        }
                    ],
                }
            ],
        },
        source="browser_cdp:dom",
    )

    assert threads["status"] == "ok"
    assert threads["source"] == "browser_cdp:dom"
    assert threads["count"] == 1
    assert threads["total_count"] == 143
    assert threads["reply_count"] == 1
    assert threads["loaded_count"] == 2
    assert threads["has_more"] is True
    assert threads["items"][0]["author"]["nickname"] == "onom"
    assert threads["items"][0]["like_count"] == 33
    assert threads["items"][0]["replies"][0]["text"] == "一般面试官问的时候会表情更严肃"


def test_comment_api_without_cookie_returns_requires_login():
    note = xhs.ParsedNote(
        note_id="65b7b9d00000000001001234",
        source_url="https://www.xiaohongshu.com/explore/65b7b9d00000000001001234",
        resolved_url="https://www.xiaohongshu.com/explore/65b7b9d00000000001001234?xsec_token=abc",
    )

    threads, warnings = asyncio.run(
        xhs._fetch_comment_threads_from_api(note, max_comments=50, cookie="")
    )

    assert threads["status"] == "requires_login"
    assert threads["auth_required"] is True
    assert threads["source"] == "api:sns/web/v2/comment/page"
    assert warnings


def test_xhs_cookie_from_file_accepts_selenium_cookie_json(tmp_path, monkeypatch):
    cookie_file = tmp_path / "xhs_cookies.json"
    cookie_file.write_text(
        json.dumps(
            {
                "version": "2.0",
                "domain": "xiaohongshu.com",
                "cookies": [
                    {"name": "a1", "value": "a1-value", "domain": ".xiaohongshu.com"},
                    {"name": "web_session", "value": "session-value", "domain": ".xiaohongshu.com"},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("HERMES_XHS_COOKIE", raising=False)
    monkeypatch.delenv("XHS_COOKIE", raising=False)
    monkeypatch.setenv("HERMES_XHS_COOKIE_FILE", str(cookie_file))

    cookie = xhs._xhs_cookie_from_env()

    assert cookie == "a1=a1-value; web_session=session-value"


def test_xhs_cookie_from_file_accepts_name_value_mapping(tmp_path, monkeypatch):
    cookie_file = tmp_path / "xhs_cookies.json"
    cookie_file.write_text(
        json.dumps({"a1": "a1-value", "web_session": "session-value", "saved_at": 1770000000}),
        encoding="utf-8",
    )
    monkeypatch.delenv("HERMES_XHS_COOKIE", raising=False)
    monkeypatch.delenv("XHS_COOKIE", raising=False)
    monkeypatch.delenv("HERMES_XHS_COOKIE_FILE", raising=False)
    monkeypatch.setenv("XHS_COOKIE_FILE", str(cookie_file))

    cookie = xhs._xhs_cookie_from_env()

    assert cookie == "a1=a1-value; web_session=session-value"


def test_xhs_inline_cookie_takes_precedence_over_cookie_file(tmp_path, monkeypatch):
    cookie_file = tmp_path / "xhs_cookies.json"
    cookie_file.write_text(json.dumps({"a1": "file-value"}), encoding="utf-8")
    monkeypatch.setenv("HERMES_XHS_COOKIE", "a1=inline-value; web_session=inline-session")
    monkeypatch.setenv("HERMES_XHS_COOKIE_FILE", str(cookie_file))

    cookie = xhs._xhs_cookie_from_env()

    assert cookie == "a1=inline-value; web_session=inline-session"


def test_extracts_stats_from_regex_interact_info_when_state_parse_is_unavailable():
    html = """
    <html>
      <body>
        <script>
          window.__OTHER__ = {"noteId":"65b7b9d00000000001001234","interactInfo":{"commentCount":"12","shareCount":"3","likedCount":"1.2万","collectedCount":"980"}};
        </script>
      </body>
    </html>
    """

    metadata = xhs._extract_metadata_from_html(
        html,
        base_url="https://www.xiaohongshu.com/explore/65b7b9d00000000001001234",
    )

    assert metadata["stats"]["status"] == "ok"
    assert metadata["stats"]["source"] == "regex:interactInfo"
    assert metadata["stats"]["like_count"] == 12000
    assert metadata["stats"]["collect_count"] == 980
    assert metadata["stats"]["comment_count"] == 12
    assert metadata["stats"]["share_count"] == 3


def test_extracts_video_metadata_from_meta_tags():
    html = """
    <html>
      <head>
        <meta property="og:type" content="video.other">
        <meta property="og:title" content="模拟面试爆款拆解 - 小红书">
        <meta property="og:description" content="前 15 秒先给面试考察点。">
        <meta property="og:image" content="https://sns-img-qc.xhscdn.com/video-cover">
        <meta property="og:video" content="https://sns-video-qc.xhscdn.com/stream/abc.mp4">
        <meta property="og:video:duration" content="28">
        <meta property="og:video:width" content="1080">
        <meta property="og:video:height" content="1920">
      </head>
    </html>
    """

    metadata = xhs._extract_metadata_from_html(
        html,
        base_url="https://www.xiaohongshu.com/explore/65b7b9d00000000001001234",
    )

    assert metadata["note_type_hint"] == "video"
    assert metadata["images"] == ["https://sns-img-qc.xhscdn.com/video-cover"]
    assert len(metadata["videos"]) == 1
    video = metadata["videos"][0]
    assert video.source_url == "https://sns-video-qc.xhscdn.com/stream/abc.mp4"
    assert video.cover_url == "https://sns-img-qc.xhscdn.com/video-cover"
    assert video.duration_ms == 28000
    assert video.width == 1080
    assert video.height == 1920


def test_extracts_video_metadata_from_initial_state():
    html = """
    <html>
      <body>
        <script>
          window.__INITIAL_STATE__ = {
            "note": {
              "videoInfo": {
                "media": {
                  "stream": {
                    "h264": [
                      {
                        "masterUrl": "https:\\/\\/sns-video-qc.xhscdn.com\\/video-a.mp4",
                        "duration": 12,
                        "width": 720,
                        "height": 1280,
                        "coverUrl": "https:\\/\\/sns-img-qc.xhscdn.com\\/video-cover-a"
                      }
                    ]
                  }
                }
              }
            }
          };
        </script>
      </body>
    </html>
    """

    metadata = xhs._extract_metadata_from_html(
        html,
        base_url="https://www.xiaohongshu.com/explore/65b7b9d00000000001001234",
    )

    assert len(metadata["videos"]) == 1
    video = metadata["videos"][0]
    assert video.source_url == "https://sns-video-qc.xhscdn.com/video-a.mp4"
    assert video.cover_url == "https://sns-img-qc.xhscdn.com/video-cover-a"
    assert video.duration_ms == 12000
    assert video.width == 720
    assert video.height == 1280
    assert "https://sns-img-qc.xhscdn.com/video-cover-a" in metadata["images"]


def test_filters_live_video_false_positive_candidates():
    html = r"""
    <html>
      <body>
        <script>
          window.__INITIAL_STATE__ = {
            "note": {
              "videoInfo": {
                "media": {
                  "stream": {
                    "h264": [
                      {
                        "masterUrl": "https:\/\/sns-video-zl.xhscdn.com\/stream\/1\/110\/258\/01e9da4fd80627aa010370019d7cce0d7e_258.mp4?sign=abc&t=123",
                        "duration": 153.946,
                        "width": 1112,
                        "height": 720
                      },
                      {
                        "backupUrls": [
                          "http:\/\/sns-video-zl.xhscdn.com\/stream\/1\/110\/258\/01e9da4fd80627aa010370019d7cce0d7e_258.mp4?sign=abc&t=123\\",
                          "http:\/\/sns-bak-v8.xhscdn.com\/stream\/1\/110\/258\/01e9da4fd80627aa010370019d7cce0d7e_258.mp4",
                          "http:\/\/sns-video-zl.xhscdn.com\/stream\/1\/110\/179\/01e9da4fd80627aa010370019d7d3b8a21_179.mp4?sign=def&t=123\\"
                        ]
                      }
                    ]
                  }
                }
              }
            }
          };
          const staticIcon = "https://fe-video-qc.xhscdn.com/fe-platform/ed8fe781ce9e16c1bfac2cd962f0721edabe2e49.ico";
          const cdnRoot = "https://sns-video-qc.xhscdn.com";
          const staticImage = "https://picasso-static.xiaohongshu.com/fe-platform/logo.png";
          const apiUrl = "https://as.xiaohongshu.com/api/sec/v1/ds?appId=xhs-pc-web";
          const pageUrl = "https://www.xiaohongshu.com/explore/69da513a0000000023005dfa";
          const cssImage = "http://sns-webpic-qc.xhscdn.com/path/image!nd_prv_wlteh_jpg_3);background-repeat:no-repeat;";
          const subtitle = "https://sns-subtitle-s10.xhscdn.com/subtitle/1/110/1/demo.srt?sign=abc\\";
        </script>
      </body>
    </html>
    """

    metadata = xhs._extract_metadata_from_html(
        html,
        base_url="https://www.xiaohongshu.com/explore/69da513a0000000023005dfa",
    )

    assert [video.source_url for video in metadata["videos"]] == [
        "https://sns-video-zl.xhscdn.com/stream/1/110/258/01e9da4fd80627aa010370019d7cce0d7e_258.mp4?sign=abc&t=123",
        "http://sns-video-zl.xhscdn.com/stream/1/110/179/01e9da4fd80627aa010370019d7d3b8a21_179.mp4?sign=def&t=123",
    ]
    assert metadata["videos"][0].duration_ms == 153946
    assert metadata["videos"][0].width == 1112
    assert metadata["videos"][0].height == 720
    assert metadata["images"] == []
    assert [subtitle.source_url for subtitle in metadata["subtitles"]] == [
        "https://sns-subtitle-s10.xhscdn.com/subtitle/1/110/1/demo.srt?sign=abc"
    ]


def test_parse_srt_text_and_prefer_chinese_subtitle():
    zh_srt = """1
00:00:00,000 --> 00:00:01,960
你好，可以听见吗？

2
00:00:01,960 --> 00:00:06,238
我叫路飞，咱们今天大概聊二十到三十分钟。
"""
    en_srt = """1
00:00:00,000 --> 00:00:01,960
Hello, can you hear me?
"""

    zh_text, zh_segments, zh_end_ms = xhs._parse_srt_text(zh_srt)
    en_text, en_segments, en_end_ms = xhs._parse_srt_text(en_srt)
    transcript = xhs._transcript_from_subtitles(
        [
            {
                "index": 1,
                "download_status": "ok",
                "language": xhs._detect_subtitle_language(en_text),
                "text": en_text,
                "segments": en_segments,
                "end_ms": en_end_ms,
            },
            {
                "index": 2,
                "download_status": "ok",
                "language": xhs._detect_subtitle_language(zh_text),
                "text": zh_text,
                "segments": zh_segments,
                "end_ms": zh_end_ms,
            },
        ],
        preferred_language="zh",
    )

    assert transcript["provider"] == "xiaohongshu_subtitle"
    assert transcript["language"] == "zh"
    assert transcript["source"] == "subtitle:2"
    assert transcript["end_ms"] == 6238
    assert "我叫路飞" in transcript["text"]


def test_chinese_transcripts_are_normalised_to_simplified():
    text, segments, _ = xhs._parse_srt_text(
        """1
00:00:00,000 --> 00:00:01,000
我做了十三年外企面試，今天聊一點視頻干貨。
"""
    )
    assert "面试" in text
    assert "视频" in text
    assert "一點" not in text
    assert segments[0]["text"] == "我做了十三年外企面试，今天聊一点视频干货。"

    transcript = xhs._normalise_transcript_payload_chinese(
        {
            "status": "ok",
            "language": "zh",
            "text": "結論先行，數據要講清楚，記得收藏幫助下期見。",
            "segments": [{"text": "為什麼做、解決了什麼問題。"}],
        }
    )
    assert transcript["script"] == "Hans"
    assert transcript["normalization"] == "traditional_to_simplified"
    assert transcript["text"] == "结论先行，数据要讲清楚，记得收藏帮助下期见。"
    assert transcript["segments"][0]["text"] == "为什么做、解决了什么问题。"


def test_write_note_files_creates_json_and_markdown(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    note = xhs.ParsedNote(
        note_id="65b7b9d00000000001001234",
        source_url="https://xhslink.com/a/test",
        resolved_url="https://www.xiaohongshu.com/explore/65b7b9d00000000001001234",
        title="秋型通勤穿搭",
        content="低饱和外套和棕色半裙。",
        stats={
            "status": "ok",
            "source": "json:interactInfo",
            "like_count": 321,
            "collect_count": 1234,
            "comment_count": 45,
            "share_count": 6,
        },
        comment_threads={
            "status": "ok",
            "source": "json:comments",
            "count": 1,
            "has_more": False,
            "cursor": "",
            "items": [
                {
                    "id": "c1",
                    "text": "求店铺名",
                    "author": {"nickname": "测试用户"},
                    "like_count": 2,
                    "time": "",
                    "ip_location": "",
                    "source": "json:comments",
                    "replies": [],
                }
            ],
        },
    )
    images = [
        {
            "index": 1,
            "source_url": "https://sns-img-qc.xhscdn.com/image-a",
            "local_path": str(tmp_path / "01.jpg"),
            "ocr_text": "秋型\\n通勤",
            "ocr_status": "ok",
        }
    ]

    note_dir, note_json_path, note_md_path, payload = xhs._write_note_files(note, images, [])

    assert note_dir.name == "65b7b9d00000000001001234"
    assert note_json_path.exists()
    assert note_md_path.exists()
    assert payload["note_id"] == "65b7b9d00000000001001234"
    saved = json.loads(note_json_path.read_text(encoding="utf-8"))
    assert saved["images"][0]["ocr_text"] == "秋型\\n通勤"
    assert saved["stats"]["collect_count"] == 1234
    assert saved["likes"] == 321
    assert saved["collects"] == 1234
    assert saved["comments"] == 45
    assert saved["comment_threads"]["items"][0]["text"] == "求店铺名"
    markdown = note_md_path.read_text(encoding="utf-8")
    assert "# 秋型通勤穿搭" in markdown
    assert "## Stats" in markdown
    assert "- collects: 1234" in markdown
    assert "## Comments" in markdown
    assert "求店铺名" in markdown
    assert "## Image OCR" in markdown
    assert "秋型\\n通勤" in markdown


def test_video_note_json_and_markdown_include_transcript(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    note = xhs.ParsedNote(
        note_id="65b7b9d00000000001001234",
        source_url="https://xhslink.com/a/test",
        resolved_url="https://www.xiaohongshu.com/explore/65b7b9d00000000001001234",
        title="模拟面试爆款拆解",
        content="前 15 秒先给面试考察点。",
        note_type="video",
        videos=[
            xhs.ParsedVideo(
                source_url="https://sns-video-qc.xhscdn.com/stream/abc.mp4",
                cover_url="https://sns-img-qc.xhscdn.com/video-cover",
                duration_ms=28000,
                width=1080,
                height=1920,
                format="mp4",
            )
        ],
    )
    video_records = [
        {
            **note.videos[0].to_record(1),
            "local_path": str(tmp_path / "01.mp4"),
            "download_status": "ok",
            "bytes": 1234,
            "content_type": "video/mp4",
        }
    ]
    audio_record = {
        "source": "video:1",
        "local_path": str(tmp_path / "01.wav"),
        "extract_status": "ok",
    }
    transcript = {
        "status": "ok",
        "provider": "test",
        "model": "fake-stt",
        "text": "这是视频逐字稿。",
        "segments": [],
    }

    _note_dir, note_json_path, note_md_path, payload = xhs._write_note_files(
        note,
        image_records=[],
        warnings=[],
        video_records=video_records,
        audio_record=audio_record,
        transcript=transcript,
    )

    saved = json.loads(note_json_path.read_text(encoding="utf-8"))
    assert payload["note_type"] == "video"
    assert saved["videos"][0]["download_status"] == "ok"
    assert saved["transcript"]["text"] == "这是视频逐字稿。"
    markdown = note_md_path.read_text(encoding="utf-8")
    assert "## Video" in markdown
    assert "## Transcript" in markdown
    assert "这是视频逐字稿。" in markdown


def test_download_video_respects_explicit_disabled_flag(tmp_path):
    note = xhs.ParsedNote(
        note_id="65b7b9d00000000001001234",
        source_url="https://xhslink.com/a/test",
        resolved_url="https://www.xiaohongshu.com/explore/65b7b9d00000000001001234",
        note_type="video",
        videos=[xhs.ParsedVideo(source_url="https://sns-video-qc.xhscdn.com/stream/abc.mp4")],
    )

    records, warnings = asyncio.run(
        xhs._download_note_videos(note, tmp_path, download_video=False, max_video_mb=100)
    )

    assert warnings == []
    assert records[0]["download_status"] == "skipped_disabled"
    assert records[0]["local_path"] == ""


def test_transcribe_video_audio_degrades_to_warning(tmp_path):
    records = [
        {
            "index": 1,
            "local_path": str(tmp_path / "01.mp4"),
            "download_status": "failed",
        }
    ]

    audio, transcript, warnings = asyncio.run(
        xhs._transcribe_video_audio(records, tmp_path, transcribe=True, stt_model=None)
    )

    assert audio["extract_status"] == "skipped_no_video_file"
    assert transcript["status"] == "skipped_no_video_file"
    assert warnings == ["Video transcription skipped: no downloaded video file is available."]


def test_video_note_prefers_chinese_subtitle_before_stt(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    note_id = "65b7b9d00000000001001234"
    html = """
    <html>
      <head>
        <meta property="og:type" content="video.other">
        <meta property="og:title" content="模拟面试爆款拆解">
        <meta property="og:description" content="先给面试考察点，再给答题框架。">
        <meta property="og:image" content="https://sns-img-qc.xhscdn.com/video-cover">
        <meta property="og:video" content="https://sns-video-qc.xhscdn.com/stream/abc.mp4">
      </head>
      <body>
        <script>
          window.__INITIAL_STATE__ = {
            "note": {
              "subtitles": [
                {"url": "https:\\/\\/sns-subtitle-s10.xhscdn.com\\/subtitle\\/en.srt?sign=abc"},
                {"url": "https:\\/\\/sns-subtitle-s10.xhscdn.com\\/subtitle\\/zh.srt?sign=abc"}
              ]
            }
          };
        </script>
      </body>
    </html>
    """

    async def fake_fetch_page(url):
        return xhs.PageFetch(
            original_url=url,
            final_url=f"https://www.xiaohongshu.com/explore/{note_id}",
            html_text=html,
            status_code=200,
        )

    async def fake_download_image(url, destination, referer):
        destination.write_bytes(b"\xff\xd8\xff\xe0fake-image")

    async def fake_download_video(url, destination, referer, max_bytes):
        destination.write_bytes(b"\x00\x00\x00\x18ftypmp42fake-video")
        return {
            "local_path": str(destination),
            "download_status": "ok",
            "bytes": destination.stat().st_size,
            "content_type": "video/mp4",
        }

    async def fake_download_subtitle(url, destination, referer):
        if "zh.srt" in url:
            text = """1
00:00:00,000 --> 00:00:01,960
你好，可以听见吗？

2
00:00:01,960 --> 00:00:06,238
我叫路飞，咱们今天大概聊二十到三十分钟。
"""
        else:
            text = """1
00:00:00,000 --> 00:00:01,960
Hello, can you hear me?
"""
        destination.write_text(text, encoding="utf-8")
        return text

    async def fail_extract_audio(*args, **kwargs):
        raise AssertionError("audio STT should be skipped when a Chinese subtitle is available")

    monkeypatch.setattr(xhs, "_fetch_page", fake_fetch_page)
    monkeypatch.setattr(xhs, "_download_image", fake_download_image)
    monkeypatch.setattr(xhs, "_download_video", fake_download_video)
    monkeypatch.setattr(xhs, "_download_subtitle", fake_download_subtitle)
    monkeypatch.setattr(xhs, "_extract_audio_from_video", fail_extract_audio)

    result = asyncio.run(
        xhs.extract_xhs_note(
            f"https://www.xiaohongshu.com/explore/{note_id}",
            ocr=False,
        )
    )

    payload = json.loads(Path(result["note_json_path"]).read_text(encoding="utf-8"))
    assert result["transcript_provider"] == "xiaohongshu_subtitle"
    assert result["transcript_language"] == "zh"
    assert result["subtitle_count"] == 2
    assert result["subtitle_language_counts"] == {"en": 1, "zh": 1}
    assert result["selected_subtitle_index"] == 2
    assert result["selected_transcript_source"] == "subtitle:2"
    assert payload["audio"]["extract_status"] == "skipped_subtitle_available"
    assert payload["subtitle_language_counts"] == {"en": 1, "zh": 1}
    assert payload["subtitles"][0]["selected_for_transcript"] is False
    assert payload["subtitles"][1]["selected_for_transcript"] is True
    assert "我叫路飞" in payload["transcript"]["text"]
    assert "- selected_for_transcript: subtitle:2" in Path(result["note_md_path"]).read_text(encoding="utf-8")


def test_default_video_note_downloads_and_transcribes_when_possible(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    note_id = "65b7b9d00000000001001234"
    html = """
    <html>
      <head>
        <meta property="og:type" content="video.other">
        <meta property="og:title" content="模拟面试爆款拆解">
        <meta property="og:description" content="先给面试考察点，再给答题框架。">
        <meta property="og:image" content="https://sns-img-qc.xhscdn.com/video-cover">
        <meta property="og:video" content="https://sns-video-qc.xhscdn.com/stream/abc.mp4">
      </head>
    </html>
    """

    async def fake_fetch_page(url):
        return xhs.PageFetch(
            original_url=url,
            final_url=f"https://www.xiaohongshu.com/explore/{note_id}",
            html_text=html,
            status_code=200,
        )

    async def fake_download_image(url, destination, referer):
        destination.write_bytes(b"\xff\xd8\xff\xe0fake-image")

    async def fake_download_video(url, destination, referer, max_bytes):
        destination.write_bytes(b"\x00\x00\x00\x18ftypmp42fake-video")
        return {
            "local_path": str(destination),
            "download_status": "ok",
            "bytes": destination.stat().st_size,
            "content_type": "video/mp4",
        }

    async def fake_extract_audio(video_path, audio_path):
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(b"fake-wav")
        return {
            "source": video_path,
            "local_path": str(audio_path),
            "extract_status": "ok",
        }

    def fake_transcribe_audio(file_path, model=None):
        assert os.environ.get("HERMES_LOCAL_STT_LANGUAGE") == "zh"
        return {
            "success": True,
            "provider": "test",
            "transcript": "先给面试考察点，再给答题框架。",
            "segments": [],
        }

    monkeypatch.setattr(xhs, "_fetch_page", fake_fetch_page)
    monkeypatch.setattr(xhs, "_download_image", fake_download_image)
    monkeypatch.setattr(xhs, "_download_video", fake_download_video)
    monkeypatch.setattr(xhs, "_extract_audio_from_video", fake_extract_audio)
    monkeypatch.setattr("tools.transcription_tools.transcribe_audio", fake_transcribe_audio)

    result = asyncio.run(
        xhs.extract_xhs_note(
            f"https://www.xiaohongshu.com/explore/{note_id}",
            ocr=False,
        )
    )

    assert result["success"] is True
    assert result["note_type"] == "video"
    assert result["video_count"] == 1
    assert result["downloaded_video_count"] == 1
    assert result["transcript_status"] == "ok"
    assert result["transcript_chars"] > 0
    payload = json.loads(Path(result["note_json_path"]).read_text(encoding="utf-8"))
    assert payload["images"][0]["role"] == "cover"
    assert payload["transcript"]["text"] == "先给面试考察点，再给答题框架。"


def test_browser_extractor_merges_visible_metadata_comments_and_transcribes_video(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("HERMES_XHS_ENABLE_BROWSER_EXTRACTOR", "1")
    note_id = "65b7b9d00000000001001234"
    html = """
    <html>
      <head>
        <meta property="og:type" content="video.other">
        <meta property="og:title" content="占位标题 - 小红书">
        <meta property="og:description" content="3 亿人的生活经验，都在小红书">
        <meta property="og:image" content="https://sns-img-qc.xhscdn.com/video-cover">
        <meta property="og:video" content="https://sns-video-qc.xhscdn.com/stream/abc.mp4">
      </head>
    </html>
    """

    async def fake_fetch_page(url):
        return xhs.PageFetch(
            original_url=url,
            final_url=f"https://www.xiaohongshu.com/explore/{note_id}",
            html_text=html,
            status_code=200,
        )

    async def fake_browser_extract(url, note_id_arg, *, extract_comments, max_comments, comment_mode):
        assert note_id_arg == note_id
        assert extract_comments is True
        return (
            {
                "status": "ok",
                "source": "hermes_browser",
                "metadata_source": "hermes_browser:visible",
                "title": "vol.13 字节UI/UX设计岗模拟面试（压力面）",
                "content": "压力面来了，前 15 秒先展示面试考察点，再给答题框架。",
                "author": {"nickname": "路飞设计沉思录"},
                "note_type": "video",
                "stats": {
                    "status": "ok",
                    "source": "hermes_browser:visible",
                    "like_count": 2802,
                    "collect_count": 314,
                    "comment_count": 143,
                    "share_count": 7,
                },
                "comment_threads": {
                    "status": "ok",
                    "source": "hermes_browser:visible",
                    "count": 1,
                    "loaded_count": 1,
                    "reply_count": 0,
                    "has_more": None,
                    "cursor": "",
                    "items": [
                        {
                            "id": "visible_1",
                            "text": "这就是压力面吗？",
                            "author": {"nickname": "onom"},
                            "like_count": 33,
                            "time": "04-12",
                            "ip_location": "北京",
                            "source": "hermes_browser:visible",
                            "replies": [],
                        }
                    ],
                },
                "snapshots": [{"role": "initial", "snapshot": "点赞 2802 收藏 314 评论 143"}],
                "screenshots": [],
                "vision_payload": {},
            },
            [],
        )

    async def fake_download_image(url, destination, referer):
        destination.write_bytes(b"\xff\xd8\xff\xe0fake-image")

    async def fake_download_video(url, destination, referer, max_bytes):
        destination.write_bytes(b"\x00\x00\x00\x18ftypmp42fake-video")
        return {
            "local_path": str(destination),
            "download_status": "ok",
            "bytes": destination.stat().st_size,
            "content_type": "video/mp4",
        }

    async def fake_extract_audio(video_path, audio_path):
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(b"fake-wav")
        return {"source": video_path, "local_path": str(audio_path), "extract_status": "ok"}

    def fake_transcribe_audio(file_path, model=None):
        return {
            "success": True,
            "provider": "test",
            "transcript": "这是中文视频逐字稿。",
            "segments": [],
        }

    monkeypatch.setattr(xhs, "_fetch_page", fake_fetch_page)
    monkeypatch.setattr(xhs, "_fetch_note_metadata_from_hermes_browser", fake_browser_extract)
    monkeypatch.setattr(xhs, "_download_image", fake_download_image)
    monkeypatch.setattr(xhs, "_download_video", fake_download_video)
    monkeypatch.setattr(xhs, "_extract_audio_from_video", fake_extract_audio)
    monkeypatch.setattr("tools.transcription_tools.transcribe_audio", fake_transcribe_audio)

    result = asyncio.run(
        xhs.extract_xhs_note(
            f"https://www.xiaohongshu.com/explore/{note_id}",
            extractor="browser",
            media_policy="safe_transcribe",
            ocr=False,
            extract_comments=True,
        )
    )

    assert result["extractor_used"] == "browser"
    assert result["media_policy"] == "safe_transcribe"
    assert result["downloaded_video_count"] == 1
    assert result["transcript_status"] == "ok"
    payload = json.loads(Path(result["note_json_path"]).read_text(encoding="utf-8"))
    assert payload["title"] == "vol.13 字节UI/UX设计岗模拟面试（压力面）"
    assert payload["likes"] == 2802
    assert payload["comment_threads"]["source"] == "hermes_browser:visible"
    assert payload["comment_threads"]["items"][0]["text"] == "这就是压力面吗？"
    assert payload["raw_metadata"]["hermes_browser"]["metadata_source"] == "hermes_browser:visible"
    assert Path(result["browser_extract_path"]).exists()


def test_default_extractor_is_ssr_media_and_does_not_call_browser(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.delenv("HERMES_XHS_NOTE_EXTRACTOR", raising=False)
    monkeypatch.delenv("HERMES_XHS_ENABLE_BROWSER_EXTRACTOR", raising=False)
    note_id = "65b7b9d00000000001001234"
    html = """
    <html>
      <head>
        <meta property="og:title" content="默认走 SSR 媒体 - 小红书">
        <meta property="og:description" content="默认不打开 Browser。">
        <meta property="og:image" content="https://sns-img-qc.xhscdn.com/a.jpg">
      </head>
    </html>
    """

    async def fake_fetch_page(url):
        return xhs.PageFetch(
            original_url=url,
            final_url=f"https://www.xiaohongshu.com/explore/{note_id}",
            html_text=html,
            status_code=200,
        )

    async def fail_browser(*args, **kwargs):
        raise AssertionError("default extraction must not call Hermes Browser")

    async def fake_download_image(url, destination, referer):
        destination.write_bytes(b"\xff\xd8\xff\xe0fake-image")

    monkeypatch.setattr(xhs, "_fetch_page", fake_fetch_page)
    monkeypatch.setattr(xhs, "_fetch_note_metadata_from_hermes_browser", fail_browser)
    monkeypatch.setattr(xhs, "_download_image", fake_download_image)

    result = asyncio.run(
        xhs.extract_xhs_note(
            f"https://www.xiaohongshu.com/explore/{note_id}",
            media_policy="metadata_only",
            ocr=False,
        )
    )

    assert result["extractor_requested"] == "ssr_media"
    assert result["extractor_used"] == "ssr_media"
    assert result["browser_extract_status"] == "skipped"
    payload = json.loads(Path(result["note_json_path"]).read_text(encoding="utf-8"))
    assert payload["raw_metadata"]["extractor"]["effective"] == "ssr_media"
    assert "hermes_browser" not in payload["raw_metadata"]


def test_browser_note_url_normalises_ios_share_page_to_pc_explore():
    note = xhs.ParsedNote(
        note_id="69eb3eb6000000001901e000",
        source_url="http://xhslink.com/o/3Bb2vbKkLNT",
        resolved_url=(
            "https://www.xiaohongshu.com/discovery/item/69eb3eb6000000001901e000"
            "?app_platform=ios&xsec_source=app_share&type=video&xsec_token=tok-123"
        ),
    )

    browser_url = xhs._browser_note_url(note)

    assert browser_url.startswith("https://www.xiaohongshu.com/explore/69eb3eb6000000001901e000?")
    assert "xsec_token=tok-123" in browser_url
    assert "xsec_source=pc_user" in browser_url
    assert "app_platform=ios" not in browser_url


def test_browser_extractor_continues_after_navigation_timeout(monkeypatch):
    monkeypatch.setenv("HERMES_XHS_ENABLE_BROWSER_EXTRACTOR", "1")
    calls = {"console": 0, "snapshot": 0, "vision": 0}

    def fake_console(clear=False, expression=None, task_id=None):
        calls["console"] += 1
        return json.dumps({"success": False, "error": "soft navigation unavailable"})

    def fake_navigate(url, task_id=None):
        assert "/explore/" in url
        return json.dumps(
            {
                "success": False,
                "error": "page.goto: Timeout 10000ms exceeded. waiting until load",
            }
        )

    def fake_snapshot(full=False, task_id=None, user_task=None):
        calls["snapshot"] += 1
        return json.dumps(
            {
                "success": True,
                "snapshot": "标题 有没有offer 3句话就能判断 点赞 7067 收藏 5116 评论 187 分享 1253",
            },
            ensure_ascii=False,
        )

    def fake_vision(question, annotate=False, task_id=None):
        calls["vision"] += 1
        return json.dumps(
            {
                "success": True,
                "screenshot_path": "",
                "analysis": json.dumps(
                    {
                        "title": "有没有offer 3句话就能判断",
                        "content": "先看岗位、作品集和表达。",
                        "note_type": "video",
                        "author": {"nickname": "路飞设计沉思录"},
                        "stats": {
                            "like_count": 7067,
                            "collect_count": 5116,
                            "comment_count": 187,
                            "share_count": 1253,
                        },
                        "comments": [],
                        "confidence": "medium",
                        "visible_evidence": "页面可见互动数据",
                    },
                    ensure_ascii=False,
                ),
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr("tools.browser_tool.browser_console", fake_console)
    monkeypatch.setattr("tools.browser_tool.browser_navigate", fake_navigate)
    monkeypatch.setattr("tools.browser_tool.browser_snapshot", fake_snapshot)
    monkeypatch.setattr("tools.browser_tool.browser_vision", fake_vision)

    payload, warnings = asyncio.run(
        xhs._fetch_note_metadata_from_hermes_browser(
            "https://www.xiaohongshu.com/explore/69eb3eb6000000001901e000",
            "69eb3eb6000000001901e000",
            extract_comments=False,
            max_comments=30,
        )
    )

    assert calls == {"console": 1, "snapshot": 1, "vision": 1}
    assert payload["status"] == "ok"
    assert payload["title"] == "有没有offer 3句话就能判断"
    assert payload["stats"]["like_count"] == 7067
    assert any("extraction continued with visible snapshot/vision evidence" in warning for warning in warnings)


def test_extract_comments_prefers_browser_cdp_dom_over_api(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    note_id = "65b7b9d00000000001001234"
    html = """
    <html>
      <head>
        <meta property="og:title" content="模拟面试爆款拆解 - 小红书">
        <meta property="og:description" content="先给面试考察点，再给答题框架。">
      </head>
    </html>
    """

    async def fake_fetch_page(url):
        return xhs.PageFetch(
            original_url=url,
            final_url=f"https://www.xiaohongshu.com/explore/{note_id}",
            html_text=html,
            status_code=200,
        )

    async def fake_fetch_from_cdp(note, *, max_comments):
        return (
            {
                "status": "ok",
                "source": "browser_cdp:dom",
                "count": 1,
                "has_more": True,
                "cursor": "",
                "items": [
                    {
                        "id": "comment_1",
                        "text": "这就是压力面吗？",
                        "author": {"nickname": "onom"},
                        "like_count": 33,
                        "time": "04-12北京",
                        "ip_location": "",
                        "source": "browser_cdp:dom",
                        "replies": [],
                    }
                ],
            },
            [],
        )

    async def fail_fetch_from_api(*args, **kwargs):
        raise AssertionError("comment API should not be called when Browser/CDP DOM has comments")

    monkeypatch.setattr(xhs, "_fetch_page", fake_fetch_page)
    monkeypatch.setattr(xhs, "_fetch_comment_threads_from_browser_cdp", fake_fetch_from_cdp)
    monkeypatch.setattr(xhs, "_fetch_comment_threads_from_api", fail_fetch_from_api)

    result = asyncio.run(
        xhs.extract_xhs_note(
            f"https://www.xiaohongshu.com/explore/{note_id}",
            extractor="ssr",
            ocr=False,
            extract_comments=True,
            allow_legacy_cdp=True,
        )
    )

    payload = json.loads(Path(result["note_json_path"]).read_text(encoding="utf-8"))
    assert payload["comment_threads"]["source"] == "browser_cdp:dom"
    assert payload["comment_threads"]["items"][0]["text"] == "这就是压力面吗？"


def test_extract_comments_does_not_call_api_when_browser_cdp_has_no_comments(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    note_id = "65b7b9d00000000001001234"
    html = """
    <html>
      <head>
        <meta property="og:title" content="模拟面试爆款拆解 - 小红书">
        <meta property="og:description" content="先给面试考察点，再给答题框架。">
      </head>
    </html>
    """

    async def fake_fetch_page(url):
        return xhs.PageFetch(
            original_url=url,
            final_url=f"https://www.xiaohongshu.com/explore/{note_id}",
            html_text=html,
            status_code=200,
        )

    async def fake_fetch_from_cdp(note, *, max_comments):
        return (
            {
                "status": "cdp_no_xhs_page",
                "source": "browser_cdp:dom",
                "count": 0,
                "loaded_count": 0,
                "has_more": False,
                "items": [],
            },
            [],
        )

    async def fail_fetch_from_api(*args, **kwargs):
        raise AssertionError("comment API fallback is disabled")

    monkeypatch.setattr(xhs, "_fetch_page", fake_fetch_page)
    monkeypatch.setattr(xhs, "_fetch_comment_threads_from_browser_cdp", fake_fetch_from_cdp)
    monkeypatch.setattr(xhs, "_fetch_comment_threads_from_api", fail_fetch_from_api)

    result = asyncio.run(
        xhs.extract_xhs_note(
            f"https://www.xiaohongshu.com/explore/{note_id}",
            extractor="ssr",
            ocr=False,
            extract_comments=True,
            allow_legacy_cdp=True,
        )
    )

    payload = json.loads(Path(result["note_json_path"]).read_text(encoding="utf-8"))
    assert payload["comment_threads"]["source"] == "browser_cdp:dom"
    assert payload["comment_threads"]["status"] == "cdp_no_xhs_page"
    assert payload["comment_threads"].get("auth_required") is None


def test_extract_note_enriches_title_and_body_from_browser_cdp_dom(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    note_id = "65b7b9d00000000001001234"
    html = """
    <html>
      <head>
        <meta property="og:title" content="占位标题 - 小红书">
        <meta property="og:description" content="3 亿人的生活经验，都在小红书">
      </head>
    </html>
    """

    async def fake_fetch_page(url):
        return xhs.PageFetch(
            original_url=url,
            final_url=f"https://www.xiaohongshu.com/explore/{note_id}",
            html_text=html,
            status_code=200,
        )

    async def fake_fetch_metadata(note):
        return (
            {
                "title": "vol.13 字节UI/UX设计岗模拟面试（压力面）",
                "content": "压力面来了，希望你不会遇到。视频模拟了字节 UI/UX 设计师面试。",
                "author": {"nickname": "路飞设计沉思录"},
                "stats": {
                    "status": "ok",
                    "source": "browser_cdp:dom",
                    "like_count": None,
                    "collect_count": None,
                    "comment_count": 143,
                    "share_count": None,
                },
                "source": "browser_cdp:dom",
            },
            [],
        )

    async def fake_fetch_comments(note, *, max_comments):
        return (xhs._comment_empty(status="cdp_no_xhs_page", source="browser_cdp:dom"), [])

    monkeypatch.setattr(xhs, "_fetch_page", fake_fetch_page)
    monkeypatch.setattr(xhs, "_fetch_note_metadata_from_browser_cdp", fake_fetch_metadata)
    monkeypatch.setattr(xhs, "_fetch_comment_threads_from_browser_cdp", fake_fetch_comments)

    result = asyncio.run(
        xhs.extract_xhs_note(
            f"https://www.xiaohongshu.com/explore/{note_id}",
            extractor="ssr",
            ocr=False,
            extract_comments=False,
            allow_legacy_cdp=True,
        )
    )

    payload = json.loads(Path(result["note_json_path"]).read_text(encoding="utf-8"))
    assert payload["title"] == "vol.13 字节UI/UX设计岗模拟面试（压力面）"
    assert payload["content"].startswith("压力面来了")
    assert payload["author"]["nickname"] == "路飞设计沉思录"
    assert payload["comments"] == 143
    assert payload["raw_metadata"]["browser_dom"]["content_chars"] > 0


def test_extracts_profile_notes_from_html_anchors_and_initial_state():
    note_id_a = "65b7b9d00000000001001234"
    note_id_b = "69da513a0000000023005dfa"
    html = f"""
    <html>
      <head>
        <meta property="og:title" content="路飞设计沉思录 - 小红书">
        <meta property="og:description" content="UIUX 设计求职与模拟面试">
      </head>
      <body>
        <a href="/explore/{note_id_a}?xsec_token=tok-a" title="148秒沉浸式校招面试模拟"></a>
        <script>
          window.__INITIAL_STATE__ = {{
            "user": {{
              "notes": [
                {{
                  "noteId": "{note_id_b}",
                  "displayTitle": "vol.13 字节UI/UX设计岗模拟面试（压力面）",
                  "type": "video",
                  "interactInfo": {{
                    "likedCount": "2802",
                    "collectedCount": "314",
                    "commentCount": "143"
                  }}
                }}
              ]
            }}
          }};
        </script>
      </body>
    </html>
    """

    parsed = xhs._extract_profile_notes_from_html(
        html,
        base_url="https://www.xiaohongshu.com/user/profile/5f5b897100000000010068c1",
    )

    assert parsed["profile"]["nickname"] == "路飞设计沉思录"
    by_id = {item["note_id"]: item for item in parsed["notes"]}
    assert by_id[note_id_a]["title"] == "148秒沉浸式校招面试模拟"
    assert by_id[note_id_a]["xsec_token"] == "tok-a"
    assert by_id[note_id_b]["title"] == "vol.13 字节UI/UX设计岗模拟面试（压力面）"
    assert by_id[note_id_b]["likes"] == 2802
    assert by_id[note_id_b]["collects"] == 314
    assert by_id[note_id_b]["comments"] == 143


def test_extract_profile_notes_writes_profile_files(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    profile_id = "5f5b897100000000010068c1"
    note_id = "69da513a0000000023005dfa"
    html = f"""
    <html>
      <head><meta property="og:title" content="路飞设计沉思录 - 小红书"></head>
      <body><a href="/explore/{note_id}?xsec_token=tok" title="压力面模拟"></a></body>
    </html>
    """

    async def fake_fetch_page(url):
        return xhs.PageFetch(
            original_url=url,
            final_url=f"https://www.xiaohongshu.com/user/profile/{profile_id}?xsec_source=pc_note",
            html_text=html,
            status_code=200,
        )

    async def fake_fetch_profile_from_cdp(*args, **kwargs):
        return ({}, [])

    monkeypatch.setattr(xhs, "_fetch_page", fake_fetch_page)
    monkeypatch.setattr(xhs, "_fetch_profile_notes_from_browser_cdp", fake_fetch_profile_from_cdp)

    result = asyncio.run(
        xhs.extract_xhs_profile_notes(
            f"https://www.xiaohongshu.com/user/profile/{profile_id}",
            prefer_cdp=True,
        )
    )

    assert result["success"] is True
    assert result["profile_id"] == profile_id
    assert result["note_count"] == 1
    saved = json.loads(Path(result["profile_json_path"]).read_text(encoding="utf-8"))
    assert saved["notes"][0]["note_id"] == note_id
    assert "压力面模拟" in Path(result["profile_md_path"]).read_text(encoding="utf-8")


def test_fetch_profile_notes_from_browser_cdp_auto_navigates(monkeypatch):
    profile_id = "5f5b897100000000010068c1"
    note_id = "69da513a0000000023005dfa"
    profile_url = f"https://www.xiaohongshu.com/user/profile/{profile_id}?xsec_source=pc_search"
    opened = []
    evaluated = []

    async def fake_fetch_targets(cdp_base_url):
        if not opened:
            return []
        return [
            {
                "type": "page",
                "url": profile_url,
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/1",
            }
        ]

    async def fake_open_target(cdp_base_url, url):
        opened.append((cdp_base_url, url))
        return {
            "type": "page",
            "url": url,
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/1",
        }

    async def fake_evaluate(ws_url, expression, *, await_promise=False):
        evaluated.append((ws_url, await_promise, "profile_dom" in expression))
        if "profile_dom" not in expression and "readyState" in expression:
            return {"href": profile_url, "readyState": "complete"}
        return {
            "profile": {
                "platform": "xiaohongshu",
                "profile_id": profile_id,
                "profile_url": profile_url,
                "nickname": "路飞设计沉思录",
            },
            "notes": [
                {
                    "note_id": note_id,
                    "url": f"https://www.xiaohongshu.com/explore/{note_id}",
                    "title": "vol.13 字节UI/UX设计岗模拟面试（压力面）",
                    "note_type": "video",
                    "stats": {
                        "status": "ok",
                        "source": "browser_cdp:profile_dom",
                        "like_count": 2802,
                        "collect_count": None,
                        "comment_count": None,
                        "share_count": None,
                    },
                    "source": "browser_cdp:profile_dom",
                }
            ],
        }

    monkeypatch.setenv("HERMES_XHS_DANGEROUS_MODE", "1")
    monkeypatch.setattr(xhs, "_cdp_http_base_url", lambda: "http://127.0.0.1:9222")
    monkeypatch.setattr(xhs, "_fetch_cdp_targets", fake_fetch_targets)
    monkeypatch.setattr(xhs, "_open_cdp_target", fake_open_target)
    monkeypatch.setattr(xhs, "_browser_cdp_runtime_evaluate", fake_evaluate)

    payload, warnings = asyncio.run(
        xhs._fetch_profile_notes_from_browser_cdp(
            profile_url,
            max_notes=20,
            scroll_rounds=2,
            auto_navigate=True,
        )
    )

    assert opened == [("http://127.0.0.1:9222", profile_url)]
    assert payload["profile"]["nickname"] == "路飞设计沉思录"
    assert payload["notes"][0]["note_id"] == note_id
    assert payload["notes"][0]["likes"] == 2802
    assert any("opened through Browser/CDP" in item for item in warnings)
    assert any(item[2] for item in evaluated)


def test_parse_profile_dom_payload_preserves_app_api_pagination_metadata():
    note_id = "673dec240000000006014e88"
    payload = {
        "profile": {"profile_id": "5f5b897100000000010068c1"},
        "app_api_pages": 1,
        "has_more": False,
        "cursor": "",
        "notes": [
            {
                "note_id": note_id,
                "url": f"https://www.xiaohongshu.com/explore/{note_id}",
                "title": "设计师毕业后去大公司还是小公司？",
                "note_type": "video",
                "stats": {
                    "status": "ok",
                    "source": "browser_cdp:app_api",
                    "like_count": 257,
                },
                "source": "browser_cdp:app_api",
            }
        ],
    }

    parsed = xhs._parse_profile_dom_payload(payload)

    assert parsed["app_api_pages"] == 1
    assert parsed["has_more"] is False
    assert parsed["notes"][0]["note_id"] == note_id
    assert parsed["notes"][0]["likes"] == 257
    assert parsed["notes"][0]["source"] == "browser_cdp:app_api"


def test_ingest_account_to_wiki_bulk_ingests_extracted_notes(tmp_path):
    wiki_path = tmp_path / "lufei-xhs-wiki"
    profile_dir = tmp_path / ".hermes" / "cache" / "xiaohongshu" / "profiles" / "5f5b897100000000010068c1"
    note_dir = tmp_path / ".hermes" / "cache" / "xiaohongshu" / "69da513a0000000023005dfa"
    profile_dir.mkdir(parents=True)
    note_dir.mkdir(parents=True)

    note_payload = {
        "platform": "xiaohongshu",
        "note_id": "69da513a0000000023005dfa",
        "note_type": "video",
        "source_url": "https://www.xiaohongshu.com/explore/69da513a0000000023005dfa",
        "title": "vol.13 字节UI/UX设计岗模拟面试（压力面）",
        "content": "压力面模拟。",
        "author": {"nickname": "路飞设计沉思录"},
        "stats": {"status": "ok", "like_count": 2802, "collect_count": 314, "comment_count": 143},
        "likes": 2802,
        "collects": 314,
        "comments": 143,
        "transcript": {"status": "ok", "text": "欢迎来到字节 UI/UX 设计岗模拟面试。"},
        "comment_threads": {"status": "ok", "items": []},
    }
    (note_dir / "note.json").write_text(json.dumps(note_payload, ensure_ascii=False), encoding="utf-8")
    (note_dir / "note.md").write_text("# vol.13 字节UI/UX设计岗模拟面试（压力面）\n", encoding="utf-8")

    profile_payload = {
        "platform": "xiaohongshu",
        "profile": {"profile_id": "5f5b897100000000010068c1", "nickname": "路飞设计沉思录"},
        "notes": [{"note_id": "69da513a0000000023005dfa", "title": note_payload["title"]}],
        "note_count": 1,
    }
    profile_json_path = profile_dir / "profile.json"
    profile_json_path.write_text(json.dumps(profile_payload, ensure_ascii=False), encoding="utf-8")
    (profile_dir / "all_notes_extraction_report.json").write_text(
        json.dumps(
            {
                "profile_id": "5f5b897100000000010068c1",
                "notes": [
                    {
                        "note_id": "69da513a0000000023005dfa",
                        "note_json_path": str(note_dir / "note.json"),
                        "note_md_path": str(note_dir / "note.md"),
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = xhs.ingest_account_to_wiki(profile_json_path=str(profile_json_path), wiki_path=str(wiki_path))

    raw_note = wiki_path / "raw" / "xhs" / "notes" / "69da513a0000000023005dfa" / "note.md"
    copied_report = wiki_path / "raw" / "xhs" / "profile" / "5f5b897100000000010068c1" / "all_notes_extraction_report.json"
    assert result["success"] is True
    assert result["ingested_notes_count"] == 1
    assert result["missing_note_artifacts_count"] == 0
    assert result["note_ingest_error_count"] == 0
    assert raw_note.exists()
    assert copied_report.exists()
    assert (wiki_path / "_derived" / "manifest.json").exists()


def test_lufei_wiki_init_ingest_manifest_and_query(tmp_path):
    wiki_path = tmp_path / "lufei-xhs-wiki"
    init_result = xhs.init_lufei_wiki(wiki_path=str(wiki_path))

    assert init_result["success"] is True
    assert (wiki_path / "persona.md").exists()
    assert (wiki_path / "raw" / "xhs" / "notes").exists()

    note_payload = {
        "platform": "xiaohongshu",
        "note_id": "69da513a0000000023005dfa",
        "note_type": "video",
        "source_url": "https://www.xiaohongshu.com/explore/69da513a0000000023005dfa",
        "resolved_url": "https://www.xiaohongshu.com/explore/69da513a0000000023005dfa?xsec_token=tok",
        "title": "vol.13 字节UI/UX设计岗模拟面试（压力面）",
        "content": "压力面来了，希望你不会遇到。",
        "author": {"nickname": "路飞设计沉思录"},
        "stats": {
            "status": "ok",
            "source": "browser_cdp:dom",
            "like_count": 2802,
            "collect_count": 314,
            "comment_count": 143,
            "share_count": 7,
        },
        "likes": 2802,
        "collects": 314,
        "comments": 143,
        "shares": 7,
        "comment_threads": {
            "status": "ok",
            "source": "browser_cdp:dom",
            "count": 1,
            "items": [
                {
                    "id": "c1",
                    "text": "压力面这个方向很有用",
                    "author": {"nickname": "用户A"},
                    "like_count": 3,
                    "replies": [],
                }
            ],
        },
        "transcript": {
            "status": "ok",
            "provider": "test",
            "language": "zh",
            "text": "我们今天来做一个字节 UI UX 设计岗压力面模拟。",
            "segments": [],
        },
    }
    note_dir = tmp_path / "note"
    note_dir.mkdir()
    note_json_path = note_dir / "note.json"
    note_json_path.write_text(json.dumps(note_payload, ensure_ascii=False), encoding="utf-8")

    ingest_result = xhs.ingest_note_to_wiki(
        note_json_path=str(note_json_path),
        wiki_path=str(wiki_path),
    )

    assert ingest_result["success"] is True
    raw_note = Path(ingest_result["raw_note_md_path"])
    assert raw_note.exists()
    raw_text = raw_note.read_text(encoding="utf-8")
    assert "source_url" in raw_text
    assert "sha256" in raw_text
    assert "字节 UI UX" in raw_text

    manifest = xhs.build_wiki_manifest(wiki_path=str(wiki_path))
    assert manifest["file_count"] > 0
    assert any(item["path"].endswith("note.md") for item in manifest["files"])

    query = xhs.query_wiki_context(
        query="字节 UIUX 压力面",
        wiki_path=str(wiki_path),
        max_files=5,
    )
    assert query["matches"]
    assert "压力面" in query["context"]


def test_query_wiki_context_prioritizes_exact_note_id(tmp_path):
    wiki_path = tmp_path / "lufei-xhs-wiki"
    raw_dir = wiki_path / "raw" / "xhs" / "notes" / "69da513a0000000023005dfa"
    raw_dir.mkdir(parents=True)
    (raw_dir / "note.md").write_text(
        "\n".join(
            [
                "---",
                'title: "vol.13 字节UI/UX设计岗模拟面试（压力面）"',
                'note_id: "69da513a0000000023005dfa"',
                "type: raw",
                "---",
                "# vol.13 字节UI/UX设计岗模拟面试（压力面）",
                "",
                "这是一条指定 note_id 的原始笔记。",
            ]
        ),
        encoding="utf-8",
    )
    staging_dir = wiki_path / "staging"
    staging_dir.mkdir(parents=True)
    (staging_dir / "reed-content-pack.md").write_text(
        "# Reed Content Pack\n\n" + ("字节 UIUX 压力面 爆款拆解 " * 100),
        encoding="utf-8",
    )

    query = xhs.query_wiki_context(
        query="69da513a0000000023005dfa 字节 UIUX 压力面",
        wiki_path=str(wiki_path),
        max_files=3,
    )

    assert query["matches"]
    assert query["matches"][0]["path"] == "raw/xhs/notes/69da513a0000000023005dfa/note.md"


def test_ingest_account_to_wiki_and_open_in_obsidian(tmp_path, monkeypatch):
    vault_path = tmp_path / "ObsidianVault"
    wiki_path = vault_path / "lufei-xhs-wiki"
    profile_id = "5f5b897100000000010068c1"
    profile_payload = {
        "platform": "xiaohongshu",
        "profile": {
            "platform": "xiaohongshu",
            "profile_id": profile_id,
            "profile_url": f"https://www.xiaohongshu.com/user/profile/{profile_id}",
            "nickname": "路飞设计沉思录",
            "description": "UIUX 设计求职与模拟面试",
        },
        "notes": [
            {
                "note_id": "69da513a0000000023005dfa",
                "url": "https://www.xiaohongshu.com/explore/69da513a0000000023005dfa",
                "title": "vol.13 字节UI/UX设计岗模拟面试（压力面）",
                "note_type": "video",
                "likes": 2802,
                "collects": 314,
                "comments": 143,
                "source": "browser_cdp:profile_dom",
            }
        ],
        "note_count": 1,
        "warnings": [],
        "extracted_at": "2026-05-22T00:00:00+00:00",
    }
    profile_json_path = tmp_path / "profile.json"
    profile_json_path.write_text(json.dumps(profile_payload, ensure_ascii=False), encoding="utf-8")

    calls = []

    def fake_which(name):
        return "/usr/local/bin/obsidian" if name == "obsidian" else None

    def fake_run_obsidian_cli(args, *, timeout=8.0):
        calls.append(args)
        if args == ["vault", "info=path"]:
            return (True, str(vault_path), "")
        if args == ["open", "path=lufei-xhs-wiki/index.md"]:
            return (True, "", "")
        return (False, "", "unexpected")

    monkeypatch.setattr(xhs.shutil, "which", fake_which)
    monkeypatch.setattr(xhs, "_run_obsidian_cli", fake_run_obsidian_cli)

    result = xhs.ingest_account_to_wiki(
        profile_json_path=str(profile_json_path),
        wiki_path=str(wiki_path),
    )

    assert result["success"] is True
    assert result["obsidian"]["inside_active_vault"] is True
    assert Path(result["raw_profile_md_path"]).exists()
    assert "字节UI/UX" in Path(result["raw_profile_md_path"]).read_text(encoding="utf-8")
    assert (wiki_path / "queries" / "account-note-inventory.md").exists()

    open_result = xhs.open_wiki_in_obsidian(wiki_path=str(wiki_path))
    assert open_result["success"] is True
    assert ["open", "path=lufei-xhs-wiki/index.md"] in calls


def test_run_content_skill_invokes_local_pipeline(tmp_path, monkeypatch):
    pipeline = tmp_path / "xhs-content-pipeline"
    runner = pipeline / "run_skill.py"
    runner.parent.mkdir(parents=True)
    runner.write_text("# runner placeholder\n", encoding="utf-8")
    input_path = tmp_path / "input.md"
    input_path.write_text("标题：压力面\n逐字稿：测试", encoding="utf-8")

    class FakeCompleted:
        returncode = 0
        stdout = "# 拆解结果\n"
        stderr = "ok\n"

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        return FakeCompleted()

    monkeypatch.setattr(xhs.subprocess, "run", fake_run)

    result = xhs.run_content_skill(
        skill="viral-analysis",
        input_path=str(input_path),
        pipeline_path=str(pipeline),
        model="claude-t0",
    )

    assert result["success"] is True
    assert result["content"] == "# 拆解结果\n"
    assert captured["cwd"] == str(pipeline)
    assert "viral-analysis" in captured["cmd"]
    assert "--model" in captured["cmd"]


def test_handler_returns_error_for_missing_xhs_url():
    raw = asyncio.run(xhs.xhs_extract_note_handler({"url": "not a xhs link"}))
    payload = json.loads(raw)

    assert payload["success"] is False
    assert "No supported Xiaohongshu" in payload["error"]


def test_plugin_registers_tool():
    calls = []

    class FakeContext:
        def register_tool(self, **kwargs):
            calls.append(kwargs)

    register(FakeContext())

    assert calls
    assert calls[0]["name"] == "xhs_extract_note"
    assert calls[0]["toolset"] == "xhs"
    assert calls[0]["is_async"] is True
    names = {call["name"] for call in calls}
    assert "xhs_extract_profile_notes" in names
    assert "xhs_ingest_note_to_wiki" in names
    assert "xhs_ingest_account_to_wiki" in names
    assert "xhs_query_wiki_context" in names
    assert "xhs_open_wiki_in_obsidian" in names


def test_human_pace_clamps_match_xhs_warning_mitigation(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.delenv("HERMES_XHS_DANGEROUS_MODE", raising=False)

    assert xhs._clamp_human_pace_comments(1000) == 50
    assert xhs._clamp_human_pace_notes(49) == 5
    assert xhs._clamp_human_pace_scroll_rounds(100) == 5

    schema_note = xhs.XHS_EXTRACT_NOTE_SCHEMA["parameters"]["properties"]
    assert schema_note["extractor"]["default"] == "ssr_media"
    assert schema_note["extract_comments"]["default"] is False
    assert schema_note["max_comments"]["maximum"] == 50
    schema_profile = xhs.XHS_EXTRACT_PROFILE_NOTES_SCHEMA["parameters"]["properties"]
    assert schema_profile["max_notes"]["maximum"] == 5
    assert schema_profile["scroll_rounds"]["maximum"] == 5


def test_human_pace_session_quota_blocks_after_5_notes(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.delenv("HERMES_XHS_DANGEROUS_MODE", raising=False)

    for i in range(xhs._HUMAN_PACE_MAX_NOTES_PER_SESSION):
        xhs._human_pace_check("test", note_id=f"note{i}")

    try:
        xhs._human_pace_check("test", note_id="note_extra")
    except PermissionError as exc:
        assert "human-pace guard" in str(exc)
    else:
        raise AssertionError("expected PermissionError after quota exhausted")

    # idempotent: re-checking a note already counted in the window is a no-op
    xhs._human_pace_check("test", note_id="note0")


def test_human_pace_dangerous_mode_bypasses_quota(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.setenv("HERMES_XHS_DANGEROUS_MODE", "1")

    # 50 notes through the quota with dangerous mode on must all pass
    for i in range(50):
        xhs._human_pace_check("test", note_id=f"note{i}")
    assert xhs._clamp_human_pace_comments(1000) == 1000
    assert xhs._clamp_human_pace_notes(49) == 49
    assert xhs._clamp_human_pace_scroll_rounds(100) == 100
