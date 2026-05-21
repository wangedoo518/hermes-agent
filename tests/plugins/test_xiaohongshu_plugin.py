import asyncio
import json
import os
from pathlib import Path

from plugins.xiaohongshu import register
from plugins.xiaohongshu import tools as xhs


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
    assert payload["audio"]["extract_status"] == "skipped_subtitle_available"
    assert "我叫路飞" in payload["transcript"]["text"]


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
            ocr=False,
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
            ocr=False,
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
            ocr=False,
            extract_comments=False,
        )
    )

    payload = json.loads(Path(result["note_json_path"]).read_text(encoding="utf-8"))
    assert payload["title"] == "vol.13 字节UI/UX设计岗模拟面试（压力面）"
    assert payload["content"].startswith("压力面来了")
    assert payload["author"]["nickname"] == "路飞设计沉思录"
    assert payload["comments"] == 143
    assert payload["raw_metadata"]["browser_dom"]["content_chars"] > 0


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
