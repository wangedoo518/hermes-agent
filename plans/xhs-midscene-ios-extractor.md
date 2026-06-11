# Midscene + iOS 拆解小红书爆款贴 — 技术方案（评审稿）

> 输入示例：
> `https://www.xiaohongshu.com/discovery/item/69da513a0000000023005dfa?app_platform=ios&app_version=9.12.2&xsec_source=app_share&type=video&xsec_token=...&xhsshare=WeixinSession`
>
> 目标：单条爆款贴 → **图文 + 视频 + 评论** 完整素材包，零 Web 风控指纹。
>
> 范围：仅替换"博主主动投喂的单条 URL"路径。**不**替换：批量主页全量、CRM、wiki 入库。
>
> 对照：[plans/xhs-midscene-extractor.md](plans/xhs-midscene-extractor.md)（Web Bridge 方案，本文为其 **iOS 双轨**）。

---

## 一、为什么走 iOS（vs Web Bridge）

| 维度 | Web Bridge | **iOS WDA** |
|---|---|---|
| 风控域 | `xiaohongshu.com` Web 风控（May 2026 警告对象） | 小红书 App 风控（**独立体系**） |
| 自动化指纹 | CDP + 扩展，仍跑在 Chrome 里 | 系统级 WDA / Accessibility，**没有 DOM 概念** |
| 视频原始流 | 走 CDN HTTPS，可拦截但是平台明令打击 | **屏幕录制**，物理截屏，与人观看不可分 |
| 图文轮播 | DOM 翻页 + img src 取证 | 原生 swipe → 每页截图 |
| 评论分页 | DOM lazy-load + `edith.xiaohongshu.com` 内部 API | 原生 swipe → 截图 → VLM 读 |
| 与路飞行为一致 | 路飞日常在 PC 看吗？大概率不 | **路飞日常在 iPhone 刷小红书**，AI 在他的 iPhone 上动 = 行为分布一致 |
| 物理节流 | 软件层 `_human_pace_check` | **物理上一次只能开一条贴**，软节流冗余 |
| 输入路径 | bridgeMode → 切 tab | 剪贴板 + Universal Link → 打开 App |

**核心：把"AI 在浏览器里点"换成"AI 在 iPhone 上滑"**。同一台路飞日常用的 iPhone，行为分布与他本人混在一起，统计上无法区分。

---

## 二、Midscene iOS 关键能力（源码核对）

| 能力 | 出处 | 用法 |
|---|---|---|
| WDA Agent 工厂 | [packages/ios/src/index.ts](file:///Users/champion/Documents/develop/midscene-main/packages/ios/src/index.ts) | `agentFromWebDriverAgent({wdaPort:8100})` |
| 启动 URL / App | [packages/ios/src/device.ts:316](file:///Users/champion/Documents/develop/midscene-main/packages/ios/src/device.ts) | `await page.launch('https://www.xiaohongshu.com/discovery/item/...')` → 走 Universal Link 进 App |
| 启动 bundle | device.ts:332 | `await page.launch('com.xingin.discover')` |
| WDA 任意 API | [docs zh/automate-with-scripts-in-yaml.mdx:339](file:///Users/champion/Documents/develop/midscene-main/apps/site/docs/zh/automate-with-scripts-in-yaml.mdx) | YAML `runWdaRequest: {method,endpoint,data}` |
| `aiQuery<T>` | core/agent.ts:1008 | 同 Web，对当前截图抽 JSON |
| `aiScroll` / 滑动 | core/agent.ts:767 | iOS 上等价于 swipe |
| 截图 | device.ts:415 `screenshotBase64()` | 每步自动；也可在 YAML 通过 `recordToReport` 落到报告 |
| 模型 | `MIDSCENE_MODEL_NAME=qwen3-vl-plus` 等 | 同 Web Bridge |
| 报告 | `generateReport: true` | HTML 回放 |

**WDA 录屏**（业内默认 appium-fork 版 WDA 提供，端点见 [Appium WDA docs](https://appium.github.io/appium-xcuitest-driver/)）：
- `POST /session/{id}/wda/screen/record/start`
- `POST /session/{id}/wda/screen/record/stop` → 返回 base64 mp4

走 Midscene 的 `runWdaRequest` 即可调用，不需要新代码。

---

## 三、端到端架构

```
路飞 Mac                                                路飞 iPhone (USB 或 同 Wi-Fi)
┌────────────────────────────────────┐              ┌────────────────────────────────┐
│ hermes (Python)                    │              │  WebDriverAgent.app             │
│  └─ plugins/xiaohongshu/           │              │   (Xcode 签名后安装)             │
│       midscene_ios_extract.py      │              │   端口 8100 listening            │
│        │                           │              │                                  │
│        │ subprocess                │              │  ┌──────────────────────────┐   │
│        ▼                           │              │  │ 小红书 App (路飞已登录) │   │
│  midscene xhs-ios-extract.yaml     │   USB/WiFi   │  │  com.xingin.discover     │   │
│   └─ @midscene/ios (Node)          │ ◀──────────▶ │  │  9.12.2                  │   │
│        └─ WDA HTTP client          │  WDA REST    │  │                          │   │
│             POST /session/url      │              │  │  - 笔记详情页              │   │
│             POST /actions/swipe    │              │  │  - 图文轮播 / 视频          │   │
│             POST /screen/record    │              │  │  - 评论 lazy-load         │   │
│             GET  /screenshot       │              │  └──────────────────────────┘   │
│                                    │              └────────────────────────────────┘
│  ─ 拉回 → out.json, video.mp4, *.png
│  ─ 本机 STT（Whisper） / OCR（hermes vision）
│  ─ 写 ~/Aurora/<creator>/cache/xhs/<note_id>/
└────────────────────────────────────┘
```

**关键：所有"敏感动作"都发生在 iPhone 上**：开 App、滑动、看视频、看评论。Mac 侧只持有 WDA 控制通道 + 截图 + 本机 STT。

---

## 四、单条爆款贴的执行流（按时间线）

| 阶段 | 动作 | 耗时 | 输出 |
|---|---|---|---|
| 0. 前置 | 路飞微信粘贴 URL → 极光识别 → `xhs_midscene_ios_extract_note(url)` | — | — |
| 1. 入 App | iPhone 剪贴板写 URL（`POST /wda/setPasteboard`）→ 启动 `com.xingin.discover` → 弹"刚才复制的链接" → `aiAct('点击进入笔记')` | 5-8s | — |
| 2. 等就绪 | `aiWaitFor: '标题、作者、互动条已加载'` | 2-5s | 首屏截图 |
| 3. 抽主体 | `aiQuery` 输出 `{title, author, publish_at, tags, body, note_type, likes, collects, comments_total, shares}` | 4-6s | `note_meta` |
| 4a. 图文分支 | 循环 `swipeLeft + screenshot` 直到 `aiBoolean('已到最后一张')` 为 true，上限 9 张 | 5-15s | `images/0.png ... N.png`，VLM 给每张做描述 |
| 4b. 视频分支 | `POST /wda/screen/record/start` → `aiWaitFor('播放进度条到末尾')`（最长 90s）→ `record/stop` 拿 mp4 → Mac 侧 ffmpeg 抽帧 + Whisper STT | 60-100s | `video.mp4`, `video.webvtt`, 关键帧 png |
| 5. 评论 | `aiTap('展开评论')` → 3-5 次 `aiScroll(direction:'up', distance:600)` → 每次 `aiQuery` 当前可见的顶层评论 → Python 侧去重合并 | 30-45s | `comments.json`（30-50 条） |
| 6. 离场 | `aiTap('返回')` 或 home | 1s | — |
| 7. 整合 | Python 把 4 个输出整合为 `note.json` + `note.md`，落 wiki | 1s | 同现有 schema 兼容 |

**总耗时**：图文 ≈ 30-50s，视频 ≈ 90-150s。

---

## 五、YAML 模板

> `scripts/midscene/xhs-ios-note-extract.yaml`

```yaml
agent:
  testId: xhs-ios-note-extract
  generateReport: true
  replanningCycleLimit: 10
  aiActContext: |
    这是小红书 iOS App 的笔记详情页（com.xingin.discover, 9.12.2）。
    若弹窗"刚才复制的链接", 点击进入。
    若有"打开通知 / 登录 / 评分"弹窗, 关闭它继续。
    页面布局: 顶部作者条 + 主体(图文轮播 OR 视频) + 互动栏(点赞/收藏/评论/分享) + 评论区(默认折叠)。
    互动数字可能是 "1.2w" 这种缩写, 输出时保留原文本同时给出数值。
  cache:
    id: xhs-ios-note-v1
    strategy: read-write

ios:
  wdaPort: 8100
  wdaHost: localhost
  autoDismissKeyboard: true
  output: ${OUT_JSON}

tasks:
  - name: 把分享 URL 写入剪贴板并打开 App
    flow:
      - runWdaRequest:
          method: POST
          endpoint: /wda/setPasteboard
          data:
            contentType: plaintext
            content: ${XHS_NOTE_URL_BASE64}
      - launch: com.xingin.discover
      - aiWaitFor: 出现"打开刚才复制的链接"提示或笔记详情页
        timeout: 8000
      - ai: 如果有"打开刚才复制的链接"提示, 点击进入; 否则忽略

  - name: 等就绪并抽主体
    flow:
      - aiWaitFor: 笔记标题、作者、点赞/收藏/评论/分享互动栏全部可见
        timeout: 10000
      - aiQuery: |
          以下面 JSON schema 输出。缺字段填 null。互动数字保留原文本与解析数值。
          {
            "title": string,
            "author": string,
            "author_id": string,
            "publish_at": string,
            "location": string | null,
            "tags": string[],
            "body": string,
            "note_type": "image" | "video",
            "stats_raw": { "likes": string, "collects": string, "comments": string, "shares": string },
            "stats": { "likes": number, "collects": number, "comments": number, "shares": number }
          }
        name: note_meta

  - name: 图文路径（仅 note_type=image 时跑）
    flow:
      - aiBoolean: 主体是图片轮播（不是视频播放器）
        name: is_image
      # is_image=true 时, 循环 swipeLeft + screenshot, 由 Midscene 的 replanning 决定何时停
      - ai: |
          如果当前是图文笔记: 把所有图片都看一遍。每张图都向左滑动一次, 直到出现"末页"提示或图片不再变化。
          每张图都帮我截图记录。最多 9 张。
        deepThink: true

  - name: 视频路径（仅 note_type=video 时跑）
    flow:
      - aiBoolean: 主体是视频播放器
        name: is_video
      - runWdaRequest:
          method: POST
          endpoint: /session/auto/wda/screen/record/start
          data:
            videoQuality: medium
            videoType: h264
      - aiWaitFor: 视频播放进度条接近 100%（视频已基本播完）
        timeout: 120000
      - runWdaRequest:
          method: POST
          endpoint: /session/auto/wda/screen/record/stop
        name: video_record_b64

  - name: 评论区
    flow:
      - aiTap: 评论入口（互动栏上的"评论"图标或顶部"全部评论"）
      - aiWaitFor: 评论列表已可见
        timeout: 5000
      - aiQuery: |
          列出当前屏幕上所有顶层评论（不含子回复）, JSON 数组按出现顺序:
          [{"author": string, "text": string, "likes": number | null, "time": string, "is_author_reply": boolean}]
        name: comments_page_1
      - aiScroll: 评论列表
        scrollType: singleAction
        direction: up
        distance: 700
      - aiQuery: |
          列出当前屏幕上所有顶层评论, 同上 schema。
        name: comments_page_2
      - aiScroll: 评论列表
        scrollType: singleAction
        direction: up
        distance: 700
      - aiQuery: 同前。
        name: comments_page_3

  - name: 离场
    flow:
      - ai: 返回笔记列表
```

**Python 侧负责**：base64 编码 URL、把 `comments_page_1..3` 合并去重、保存 `video_record_b64` → mp4 → Whisper STT、生成 `note.md`。

---

## 六、Python 封装

```python
# plugins/xiaohongshu/midscene_ios_extract.py
"""Midscene + iOS WDA extractor for one Xiaohongshu note.

Runs against the user's own iPhone over WDA. Produces the same note.json/
note.md shape as plugins/xiaohongshu/tools.py:extract_xhs_note so downstream
wiki/skill consumers are unchanged.
"""

from __future__ import annotations

import asyncio, base64, json, os, shutil, subprocess, tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
YAML_PATH = REPO_ROOT / "scripts" / "midscene" / "xhs-ios-note-extract.yaml"


XHS_MIDSCENE_IOS_EXTRACT_SCHEMA = {
    "name": "xhs_midscene_ios_extract_note",
    "description": (
        "Extract one Xiaohongshu note (title, body, tags, images, video, "
        "transcript, top comments) by driving the user's own iPhone via "
        "Midscene + WebDriverAgent. Pure-vision, no DOM, no internal XHS "
        "Web API. Requires WDA running on port 8100 and the XHS app logged "
        "in on the device. Counts against the human-pace quota."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "XHS share URL."},
            "wda_port": {"type": "integer", "default": 8100},
            "wda_host": {"type": "string", "default": "localhost"},
            "model": {"type": "string", "description": "Override MIDSCENE_MODEL_NAME."},
            "transcribe_video": {"type": "boolean", "default": True},
            "max_comments": {"type": "integer", "default": 30, "minimum": 0, "maximum": 50},
        },
        "required": ["url"],
    },
}


async def midscene_ios_extract_note(
    url: str,
    *,
    wda_port: int = 8100,
    wda_host: str = "localhost",
    model: str | None = None,
    transcribe_video: bool = True,
    max_comments: int = 30,
) -> dict[str, Any]:
    if not YAML_PATH.exists():
        raise FileNotFoundError(YAML_PATH)

    from plugins.xiaohongshu.tools import (
        _human_pace_check, _extract_note_id_from_url, _xhs_cache_root, _safe_note_id,
    )
    note_id = _extract_note_id_from_url(url) or url
    _human_pace_check("midscene_ios_note", note_id=note_id)

    note_dir = _xhs_cache_root() / _safe_note_id(note_id)
    note_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="midscene-ios-xhs-") as tmp:
        out_json = Path(tmp) / "result.json"
        env = {
            **os.environ,
            "XHS_NOTE_URL": url,
            "XHS_NOTE_URL_BASE64": base64.b64encode(url.encode()).decode(),
            "OUT_JSON": str(out_json),
            "MIDSCENE_WDA_PORT": str(wda_port),
            "MIDSCENE_WDA_HOST": wda_host,
            "MIDSCENE_MODEL_NAME": model or os.environ.get("MIDSCENE_MODEL_NAME") or "qwen3-vl-plus",
        }
        cmd = ["npx", "@midscene/cli", str(YAML_PATH)]
        proc = await asyncio.create_subprocess_exec(
            *cmd, env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0 or not out_json.exists():
            raise RuntimeError(
                f"midscene ios failed (code={proc.returncode}): "
                f"{stderr.decode(errors='replace')[-1200:]}"
            )
        payload = json.loads(out_json.read_text(encoding="utf-8"))

    # Merge & dedupe comments across pages
    seen = set(); comments: list[dict] = []
    for key in ("comments_page_1", "comments_page_2", "comments_page_3"):
        for c in payload.get(key) or []:
            sig = (c.get("author", ""), c.get("text", "")[:80])
            if sig in seen: continue
            seen.add(sig); comments.append(c)
            if len(comments) >= max_comments: break

    # Video → mp4 → STT
    video_path = ""; transcript = ""
    if payload.get("video_record_b64"):
        video_path = str(note_dir / "video.mp4")
        Path(video_path).write_bytes(base64.b64decode(payload["video_record_b64"]))
        if transcribe_video:
            from tools.transcription_tools import transcribe_audio  # existing hermes helper
            transcript = await transcribe_audio(video_path, language="zh")

    meta = payload.get("note_meta") or {}
    result = {
        "success": True,
        "note_id": note_id,
        "source": "midscene:ios-wda",
        "title": meta.get("title", ""),
        "author": meta.get("author", ""),
        "body": meta.get("body", ""),
        "tags": meta.get("tags") or [],
        "note_type": meta.get("note_type"),
        "stats": meta.get("stats") or {},
        "stats_raw": meta.get("stats_raw") or {},
        "video_path": video_path,
        "transcript": transcript,
        "comment_threads": {
            "source": "midscene:ios-wda",
            "items": comments[:max_comments],
            "count": len(comments[:max_comments]),
        },
        "output_dir": str(note_dir),
    }
    # Persist to disk in same shape as legacy extract
    (note_dir / "note.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
```

集成方式：
1. 注册到 `plugins/xiaohongshu/__init__.py`
2. 在 `_xhs_note_extractor()` 路由里加 `"ios"` 选项；`HERMES_XHS_NOTE_EXTRACTOR=ios` 启用
3. 默认路由：`auto` 模式 = **先 iOS（路飞日常设备），扩展未连接才走 Web Bridge，再 fallback 旧 CDP，再 SSR**

---

## 七、视频取证细节

WDA 录屏要点（评审需当场确认）：
1. **必须 appium-WDA** 而非 facebook 原版。Midscene 文档要求 WDA ≥ 7.0.0（[prepare-ios.mdx](file:///Users/champion/Documents/develop/midscene-main/apps/site/docs/zh/common/prepare-ios.mdx)），appium fork 7.x 已含 `/screen/record/start|stop`。
2. **视频码率**：`videoQuality: medium` 平衡文件大小，1 分钟视频约 15-30 MB。
3. **录屏期间不能切前台**：所以视频任务设计为「先录完再去评论」，不能并行。
4. **音轨**：WDA 录屏含设备音频（小红书 App 视频原生播放声音）。STT 用 hermes 现有 `transcribe_audio`。
5. **视频比 Web 优势**：Web 走 m3u8 / mp4 CDN 取证 → 风控明确打击；iOS 录屏 = 物理捕获，零网络异常。

---

## 八、风险与缓解

| 风险 | 缓解 |
|---|---|
| WDA 签名 7 天过期（个人 Apple ID） | 路飞用付费开发者账号（¥688/年）→ WDA 1 年；或者用 free 账号 + 每周自动重签脚本 |
| iPhone 锁屏 / 推送中断 | 进入"专注模式"或专用"AI 工作"专注，关掉来电视频；执行前自动唤醒 |
| 物理速率慢（视频 90s）| 在 SKU "fanout=50" 里把视频帧速率改为先取前 30s + 末 30s + 中点; 全长仅在主推贴上跑 |
| 多设备并发？ | 多 iPhone + 不同 WDA port。一台只能一条贴。**这是 feature 不是 bug**：天然合规。|
| 苹果系统升级 / WDA 不兼容 | 锁路飞那台 iPhone 不升级；备机 iPad 跑 iPadOS 同 build |
| iOS Universal Link 弹窗"打开小红书?" | 第一次手动点"始终允许"；后续 silent |
| 剪贴板写入需要 WDA 权限 | iOS 15+ 限制 → 改用 `xhsdiscover://item/<noteId>` URL scheme 直跳（更稳） |
| 路飞 iPhone 隐私 | Mac 与 iPhone 同一局域网 (`wdaHost` 为内网 IP)，**不上云**；录屏只落本机 |

---

## 九、成本

按 50 条爆款 / 天估算（图文:视频 = 3:2）：

| 资源 | 单条 | 50 条/天 | 备注 |
|---|---|---|---|
| Midscene VLM 调用 (qwen3-vl-plus) | ~6 次 aiQuery + ~10 次 ai/aiBoolean | ≈ ¥4 | 缓存命中后 ≈ ¥2 |
| Whisper STT（视频 30s 平均） | 本机 GPU 0 元；走 API ≈ ¥0.5/分钟 | ≈ ¥10 | 用本机 fast-whisper 直接 0 元 |
| WDA / iPhone 折旧 | — | 0 | 路飞自有 |
| 阿里云 Mac 云（可选） | 选用 | ¥1500/月 | 仅在路飞不愿挂自己 Mac 时 |
| **小计** | | **¥4-14/天** | 比 Web 方案略高，但取了视频 + 评论 + 图文全套 |

vs Web Bridge 方案：贵 5 倍，但**风控等级低一个数量级** + **视频完整**。

---

## 十、与现有方案的关系

| 文档 | 关系 |
|---|---|
| [xhs-midscene-extractor.md](plans/xhs-midscene-extractor.md) | Web Bridge 方案 = **fallback 路径**（路飞 PC 在家时） |
| [xhs-ingestion-replacement.md](plans/xhs-ingestion-replacement.md) | 本方案是 Tier-0 的新成员（不挂 Web 账号 → 不触发 May 2026 警告类规则） |
| [xhs-creator-ip-skus.md](plans/xhs-creator-ip-skus.md) | SKU "拆爆款 fanout=50" 主数据源升级为 iOS；W6 时间表不变 |
| [plugins/xiaohongshu/tools.py 人速节流](plugins/xiaohongshu/tools.py) | 沿用同一个 `_human_pace_check`，三条路径共享 5 条/小时配额 |

---

## 十一、落地里程碑

| 周次 | 任务 | 验收 |
|---|---|---|
| **W1 D1-2** | 路飞 Mac 装 Xcode；为 iPhone 签 appium-WDA 7.x | `curl http://localhost:8100/status` 返回 `ready: true` |
| **W1 D3** | `npx @midscene/ios-playground` 跑通：在 App 里抽一条笔记的 title | playground 中 `aiQuery` 返回正确 JSON |
| **W1 D4** | 写 `scripts/midscene/xhs-ios-note-extract.yaml` | 走 share URL 全流程跑通 1 条**图文**笔记 |
| **W1 D5** | 同上跑通 1 条**视频**笔记 + Whisper STT | `video.mp4` + `video.webvtt` 写到 cache |
| **W2 D1-2** | `plugins/xiaohongshu/midscene_ios_extract.py` + 路由 `auto` 加 ios 选项 | `hermes` 命令调用，与 SKU fanout 工作流对接 |
| **W2 D3** | 评论分页去重 + 上限 30/50 | 真实热门贴抽到 ≥ 30 条评论 |
| **W2 D4-5** | 拆 50 条爆款压力测试 | 24h 内跑完，单条平均 ≤ 2 min；零警告 |
| **W3** | 录屏自动化兜底（前台保活、推送静音脚本） | 一晚 zero-touch 跑 30 条不中断 |

---

## 十二、待 azan / ariel / 陶双 拍板（5 条）

1. **专用 iPhone 还是路飞日常机？** —— 推荐**专用机 + 路飞登录小号**，避免 24h 跑 AI 影响他正常用机；同时小号触发限制不影响主号。
2. **每天 50 条爆款的视频是否都完整录？** —— 建议**主推贴录全长（按"拆爆款 Top 5"标注）**，其余只录"前 30s + 末 30s + 中点 10s"。
3. **多机并行？** —— 5 台便宜 iPhone SE + 5 个 WDA port → 50 条 / 天 / 设备并行。但 azan 拍过「闭门造车 MVP」，建议先 1 台跑通。
4. **是否同时保留 Web Bridge 作 fallback？** —— 推荐保留：路飞出差不带 iPhone 时仍可用 Mac Chrome。`HERMES_XHS_NOTE_EXTRACTOR=auto` 内置二选一。
5. **WDA 签名方案：付费开发者账号（¥688/年）vs 每周重签** —— 推荐付费账号，省运维。

---

## 十三、一句话结论

> **把"拆爆款"这件事从 Chrome 搬到 iPhone**。同一台路飞日常用的设备、同一套 Midscene API、同一个人速节流闸门；输出与现有 `extract_xhs_note` 字段 100% 兼容，下游 wiki / SKU 完全不动。增量代码 ≈ 1 个 YAML + 1 个 Python wrapper（~200 行）+ 1 周 WDA 环境搭建。换来的是：**视频本地录屏 + 评论原生分页 + 零 Web 风控**。
