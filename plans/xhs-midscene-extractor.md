# 用 Midscene 替代 DOM 抓取做爆款贴拆解 — 技术方案

> 背景：[plans/xhs-ingestion-replacement.md](plans/xhs-ingestion-replacement.md) 把 DOM/CDP 抓取从「极高风险」降到「中风险（已节流）」。本文进一步把**爆款贴拆解**这一条路径切到 [Midscene](https://github.com/web-infra-dev/midscene)（`/Users/champion/Documents/develop/midscene-main`），用**纯视觉 + VLM**取代当前的 DOM JS 注入和内部 API 直调。
>
> 范围：仅替换"博主主动投喂的单条 URL → note.json/note.md"路径（SKU "拆爆款 fanout=50" 的前端数据源）。**不**替换：批量主页全量抓取（已删/禁）、CRM、wiki 入库、SSR 公开页 fallback。

---

## 一、为什么是 Midscene（vs 现有 CDP DOM-walker）

| 维度 | 现有实现（[plugins/xiaohongshu/tools.py](plugins/xiaohongshu/tools.py)） | Midscene Bridge Mode | 风控影响 |
|---|---|---|---|
| 元素定位 | `Runtime.evaluate` 注入大块 JS，遍历 `window.__INITIAL_STATE__` 和 DOM 树 | 纯视觉，对截图调 VLM 找元素坐标 | 🔴→🟢 无 DOM walker 指纹 |
| 评论数据来源 | 直调 `edith.xiaohongshu.com/api/sns/web/v2/comment/page` | 滚动评论区，让 VLM 读屏 | 🔴→🟢 无内部 API hit |
| 分页 / 滚动 | `_profile_dom_extractor_js` 硬编码 `scroll_rounds=100` | `aiScroll` 单次一段，模型自判是否再滚 | 🔴→🟢 滚动节奏与人相同 |
| 浏览器 | 用户登录态 Chrome（CDP） | 用户登录态 Chrome（Midscene 扩展 + WebSocket bridge） | 🟡→🟡 同 |
| 数据 schema | 写死字段 → XHS 改版即坏 | prompt 描述 schema，VLM 适配 A/B | 🔴→🟢 抗版本 |
| 缓存 | 无 | `cache.id` 缓存 planner + 元素定位（[caching.mdx](file:///Users/champion/Documents/develop/midscene-main/apps/site/docs/zh/caching.mdx)）→ 50 张爆款拆解时 49 张走缓存 | — |
| 调用栈 | Python CDP WS → JS → DOM API | Python → Node CLI → Midscene Agent → 扩展 → CDP → 截图 | 多一跳，但 100% 移除我方的 DOM 指纹 |

**核心洞察**：上次小红书警告的"自动浏览/查看"高度匹配的就是 `Runtime.evaluate` + 大段 DOM 遍历 JS + edith 内部 API。Midscene 走的是**人类视角**：截图 → VLM 找元素 → 点 / 滚动 / 截图，**没有任何注入脚本、没有任何内部 API hit**。

---

## 二、Midscene 关键能力（从源码确认）

| 能力 | 出处 | 用法 |
|---|---|---|
| Bridge Mode（复用用户登录 Chrome） | [packages/web-integration/src/bridge-mode/agent-cli-side.ts:206](file:///Users/champion/Documents/develop/midscene-main/packages/web-integration/src/bridge-mode/agent-cli-side.ts) | `agent.connectCurrentTab()` 或 `agent.connectNewTabWithUrl(url)` |
| `aiQuery<T>(demand)` | [packages/core/src/agent/agent.ts:1008](file:///Users/champion/Documents/develop/midscene-main/packages/core/src/agent/agent.ts) | prompt 中描述 JSON schema，返回结构化数据 |
| `aiScroll(prompt, opt)` | agent.ts:767 | `singleAction` / `scrollToBottom` 两种粒度 |
| `aiWaitFor` | agent.ts:1286 | 等评论区渲染好再 query |
| YAML 模式 | [apps/site/docs/zh/automate-with-scripts-in-yaml.mdx:471](file:///Users/champion/Documents/develop/midscene-main/apps/site/docs/zh/automate-with-scripts-in-yaml.mdx) | `midscene <yaml>` CLI 一行启动 |
| Bridge YAML 开关 | `web.bridgeMode: currentTab | newTabWithUrl` + `closeNewTabsAfterDisconnect` | 一行打开 bridge |
| 缓存 | [apps/site/docs/zh/caching.mdx](file:///Users/champion/Documents/develop/midscene-main/apps/site/docs/zh/caching.mdx) | `agent.cache.id` 写 `./midscene_run/cache/*.cache.yaml` |
| 模型选择 | [model-config.mdx](file:///Users/champion/Documents/develop/midscene-main/apps/site/docs/zh/model-config.mdx) | `MIDSCENE_MODEL_NAME=qwen3-vl-plus` + `MIDSCENE_MODEL_API_KEY` + `MIDSCENE_MODEL_BASE_URL`；OpenAI 兼容 |
| 报告 | `generateReport: true` | HTML 回放报告，含每步截图，便于复盘 |

---

## 三、集成架构

```
hermes (Python)
  └─ plugins/xiaohongshu/midscene_extract.py            ← 新建
       └─ subprocess: npx midscene path/to/note.yaml
            └─ AgentOverChromeBridge (Node)
                 └─ WebSocket 127.0.0.1:3766
                      └─ Midscene Chrome 扩展（用户已登录的桌面 Chrome）
                           └─ XHS 笔记详情页
                                └─ VLM 看屏 → 滚动 → aiQuery → JSON
            ← stdout 解析 → note.json / note.md
```

**为什么 subprocess + YAML 而不是直接绑 Node**：
1. hermes 是 Python，Midscene 是 TypeScript。最低耦合 = 跨进程
2. YAML 声明式，diff 友好，便于沉淀 SOP
3. Midscene 内置 `output: path/to/result.json` 参数，hermes 只需读这个文件
4. 升级 Midscene 与升级 hermes 解耦

后续如需高频调用 / 减少冷启动，再加 **Option B**：长驻 Node sidecar + 简单 stdin RPC（[packages/web-integration/src/bin.ts](file:///Users/champion/Documents/develop/midscene-main/packages/web-integration/src/bin.ts)）。

---

## 四、爆款贴拆解 YAML 模板

> 路径：`scripts/midscene/xhs-note-extract.yaml`

```yaml
agent:
  testId: xhs-note-extract
  generateReport: true
  replanningCycleLimit: 8
  aiActContext: |
    这是小红书笔记详情页。布局通常为：
    - 顶部：作者头像、昵称、发布时间、关注按钮
    - 中部：笔记正文区，图文笔记是图片轮播 + 多段文字；视频笔记是视频播放器 + 简短文案
    - 标签：以 #xxx 形式出现在正文末尾
    - 互动条：点赞、收藏、评论、分享数（图标+数字）
    - 评论区：默认折叠或仅显示前几条，需要滚动加载更多
    如果遇到登录弹窗或"打开小红书 App"提示，关掉它继续。

  cache:
    id: xhs-note-v1          # ← 同一布局复用缓存，第二条贴 90% 命中
    strategy: read-write

web:
  url: ${XHS_NOTE_URL}        # ← 从 hermes Python 侧通过 env 注入
  bridgeMode: currentTab      # 或 newTabWithUrl，第一版用 currentTab，路飞自己点开后再触发
  output: ${OUT_JSON}

tasks:
  - name: 抽取笔记主体
    flow:
      - aiWaitFor: 笔记标题与正文已渲染完毕
        timeout: 8000
      - aiQuery: |
          以下面这个 JSON schema 输出（缺字段写 null，不要编）：
          {
            "title": string,
            "author": string,
            "author_id": string,
            "publish_at": string,        // 原始时间文本，例如 "05-22 北京"
            "location": string | null,
            "tags": string[],
            "body": string,              // 笔记正文（全文，按换行保留）
            "note_type": "image" | "video",
            "image_count": number,
            "video_present": boolean,
            "likes": number | null,
            "collects": number | null,
            "comments_total": number | null,
            "shares": number | null
          }
        name: note_meta

  - name: 渐进式抽取前 30 条评论（人速）
    flow:
      - aiScroll: 评论区
        scrollType: singleAction
        direction: down
        distance: 600
      - aiWaitFor: 评论区出现至少 3 条评论
        timeout: 5000
      - aiScroll: 评论列表
        scrollType: singleAction
        direction: down
        distance: 800
      - aiScroll: 评论列表
        scrollType: singleAction
        direction: down
        distance: 800
      - aiQuery: |
          列出当前可见的所有顶层评论（不含回复），按出现顺序，JSON 数组：
          [{
            "author": string,
            "text": string,
            "likes": number | null,
            "time": string,           // 原始相对时间，例如 "2 天前"
            "is_author_reply": boolean
          }]
          最多 30 条。
        name: comments_top30
```

**关键设计**：
- **3 次 `aiScroll` 上限**，每次 600-800px，对应人速节流（[xhs-ingestion-replacement.md Tier 2](plans/xhs-ingestion-replacement.md)）
- **`aiWaitFor` 软同步**取代轮询，由 VLM 判断 DOM 是否到位
- **`cache.id: xhs-note-v1`** —— 同一版本 XHS 布局拆 50 条爆款时，planner + 评论区定位走缓存，每条只剩 2 次 `aiQuery` 的 VLM 调用（model token 成本可控）
- **`output` 写到 hermes 指定的临时 JSON** —— Python 侧直接 `json.load`

---

## 五、Python 侧封装

### 5.1 新 tool：`xhs_midscene_extract_note`

```python
# plugins/xiaohongshu/midscene_extract.py
"""Midscene-driven Xiaohongshu note extractor (vision-only).

Bypasses the legacy CDP DOM walker for share-url ingest. Runs the bundled
`scripts/midscene/xhs-note-extract.yaml` against a logged-in Chrome via the
Midscene Bridge extension. No `Runtime.evaluate` on Xiaohongshu pages, no
edith.xiaohongshu.com API hits.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
YAML_PATH = REPO_ROOT / "scripts" / "midscene" / "xhs-note-extract.yaml"


XHS_MIDSCENE_EXTRACT_SCHEMA = {
    "name": "xhs_midscene_extract_note",
    "description": (
        "Extract one Xiaohongshu note (title, body, tags, stats, top "
        "comments) using the Midscene Chrome extension + VLM. Uses pure "
        "vision — no DOM JS injection, no internal API calls. Requires the "
        "Midscene browser extension installed and the desktop Chrome logged "
        "in to Xiaohongshu. Hard-limited to the human-pace window."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Single XHS note URL."},
            "model": {
                "type": "string",
                "description": (
                    "Override MIDSCENE_MODEL_NAME for this call. Default: "
                    "qwen3-vl-plus (cheap, fast, Chinese-native)."
                ),
            },
            "max_comments": {
                "type": "integer",
                "description": "Cap on comments returned. Hard-capped at 30.",
                "default": 30,
                "minimum": 0,
                "maximum": 30,
            },
        },
        "required": ["url"],
    },
}


async def midscene_extract_note(
    url: str,
    *,
    model: str | None = None,
    max_comments: int = 30,
) -> dict[str, Any]:
    if not YAML_PATH.exists():
        raise FileNotFoundError(YAML_PATH)
    if not shutil.which("npx") and not shutil.which("midscene"):
        raise RuntimeError(
            "midscene CLI not found. Run: npm i -g @midscene/cli "
            "OR ensure npx is on PATH."
        )
    # Reuse the same human-pace guard as the CDP path
    from plugins.xiaohongshu.tools import (
        _human_pace_check,
        _extract_note_id_from_url,
    )
    note_id = _extract_note_id_from_url(url) or url
    _human_pace_check("midscene_note", note_id=note_id)

    with tempfile.TemporaryDirectory(prefix="midscene-xhs-") as tmp:
        out_json = Path(tmp) / "result.json"
        env = {
            **os.environ,
            "XHS_NOTE_URL": url,
            "OUT_JSON": str(out_json),
            "MIDSCENE_MODEL_NAME": model or os.environ.get("MIDSCENE_MODEL_NAME") or "qwen3-vl-plus",
        }
        cmd = [
            shutil.which("midscene") or "npx",
            *([] if shutil.which("midscene") else ["@midscene/cli"]),
            str(YAML_PATH),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0 or not out_json.exists():
            raise RuntimeError(
                f"midscene failed (code={proc.returncode}): "
                f"{stderr.decode(errors='replace')[-800:]}"
            )
        payload = json.loads(out_json.read_text(encoding="utf-8"))

    meta = payload.get("note_meta") or {}
    comments = (payload.get("comments_top30") or [])[: max(0, min(max_comments, 30))]
    return {
        "success": True,
        "note_id": note_id,
        "source": "midscene:bridge",
        "title": meta.get("title", ""),
        "author": meta.get("author", ""),
        "body": meta.get("body", ""),
        "tags": meta.get("tags") or [],
        "stats": {
            "likes": meta.get("likes"),
            "collects": meta.get("collects"),
            "comments": meta.get("comments_total"),
            "shares": meta.get("shares"),
        },
        "note_type": meta.get("note_type"),
        "image_count": meta.get("image_count"),
        "video_present": bool(meta.get("video_present")),
        "comment_threads": {
            "source": "midscene:bridge",
            "items": [
                {"text": c.get("text", ""), "author": c.get("author", ""),
                 "likes": c.get("likes"), "time": c.get("time", "")}
                for c in comments
            ],
            "count": len(comments),
        },
    }
```

### 5.2 注册

在 `plugins/xiaohongshu/__init__.py` 把 `XHS_MIDSCENE_EXTRACT_SCHEMA` + handler 加到 `provides_tools` 与 `tool_registry`。

### 5.3 现有 `xhs_extract_note` 的路由策略

引入 **provider 选择器**：

```python
# in plugins/xiaohongshu/tools.py
def _xhs_note_extractor() -> str:
    return (os.environ.get("HERMES_XHS_NOTE_EXTRACTOR") or "auto").strip().lower()
```

- `auto`（默认）：先试 Midscene；扩展未连接 / CLI 报错时退回当前 CDP；再 fallback 到 SSR 公开页
- `midscene`：强制 Midscene，失败抛错
- `cdp`：旧路径（仍受人速节流）
- `ssr`：仅匿名 SSR，零账号风控

---

## 六、模型与成本

按 50 条爆款拆解 / 天估算：

| 模型 | 每条贴调用 | 单调用粗估 token in/out | 50 条/天 token | 备注 |
|---|---|---|---|---|
| `qwen3-vl-plus`（默认）| `aiQuery×2` + planner 缓存 | ~3k in / 800 out | 380k token | 阿里云，国内最便宜的 VLM |
| `doubao-1.6-vision` | 同上 | ~3k / 800 | 380k token | 字节，与 XHS 中文场景契合度高 |
| `gemini-3-pro` | 同上 | ~3k / 800 | 380k token | 走 OpenAI 兼容代理，绝对质量最高 |
| `UI-TARS-1.5-7B` | 同上 | 自部署 | 0 token 费 | 单机 GPU；若以后量大可上 |

**缓存命中率**：第一次跑 1 条 → 写 `xhs-note-v1.cache.yaml` → 后 49 条 planner + 评论区定位走缓存 → 只剩 2 次 `aiQuery`（query 永不缓存）。冷启动后单条 < ¥0.05（按 qwen3-vl-plus 价目）。

---

## 七、与人速节流的集成（继承上一轮工作）

Midscene 路径**沿用同一个** `_human_pace_check`：
- 5 条笔记 / 滚动 1 小时窗口 — **CDP 和 Midscene 共享**（`note_id` 幂等）
- 这样路飞一天最多被 AI 触发 5×24 = 120 次访问，每次都是人类节奏
- `HERMES_XHS_DANGEROUS_MODE=1` 同时解除两条路径上限

Midscene 内部的 `aiScroll` 步骤本身就是「单次一段」+ VLM 等待，**不需要**在 Python 侧再插入 jitter，但仍保留 `_human_pace_jitter` 作为 CDP fallback 路径的兜底。

---

## 八、抗版本设计（路飞的护城河之一）

XHS 频繁 A/B 改版。当前实现的 schema 用了写死的 `_NOTE_DOM_METADATA_JS` 字符串解析 `window.__INITIAL_STATE__` —— 任何字段名改动都坏。

Midscene 方案：
1. YAML 里的 prompt 描述「**业务字段**」而不是 DOM 节点
2. VLM 在新布局上仍然能看懂"这是点赞数"
3. 出现极端版本变动时，**只需要改 prompt 里的 schema 描述**，不动代码
4. 旧路径作 fallback，保证迁移期不全断

---

## 九、安全 / 隐私

| 风险 | 缓解 |
|---|---|
| Midscene 截图含个人信息 | `./midscene_run/` 在博主本机；hermes 不上传；定期清理（cron） |
| 报告 HTML 含截图 | `generateReport: false` 在生产模式下默认关；开发模式下打开 |
| 扩展通讯走 `127.0.0.1:3766` | 默认本机 only；远程访问要显式 `allowRemoteAccess` |
| VLM 调用上行带截图 | 走阿里云 / 火山 / Nous Portal（自选），不上传我方服务器 |
| 缓存文件含元素路径 | 只在博主本机；定期 prune 旧 cache（超 30 天） |

---

## 十、落地里程碑

| 周次 | 任务 | 验收 |
|---|---|---|
| W0（今天） | 写本文档；ariel/陶双评审 | 确认 prompt schema 与缓存策略 |
| W1 | 装 Midscene 扩展到路飞本机；`scripts/midscene/xhs-note-extract.yaml` 跑通 1 条 | `midscene xhs-note-extract.yaml` 输出完整 JSON |
| W1 | 写 `plugins/xiaohongshu/midscene_extract.py` + 注册 | `xhs_midscene_extract_note` 在 `hermes tool list` 可见 |
| W2 | `HERMES_XHS_NOTE_EXTRACTOR=auto` 路由切换 | 默认走 Midscene，CDP 失败自动 fallback |
| W2 | `xhs-note-extract.yaml` 接入 SKU-A/SKU-B 的拆爆款链路 | fanout 50 条 ≤ 2 小时跑完，平均单条 < ¥0.05 |
| W3 | 缓存效率测试：跑 100 条统计命中率与耗时 | 第 2 条以后单条 ≤ 30s |
| W4 | 旧 `_NOTE_DOM_METADATA_JS` / `_comment_dom_extractor_js` / `edith.xiaohongshu.com` 调用全部废弃 | grep 仓库已无相关字符串引用（除非 dangerous mode） |
| W5 | 长驻 Node sidecar（Option B 优化） | 单条冷启动从 8s → 2s |

---

## 十一、关键决策（团队评审待拍）

1. **默认 VLM 用什么？** —— 推荐 `qwen3-vl-plus`（中文好 + 便宜）；备选豆包；高质量 fallback Gemini-3-Pro。是否走 Nous Portal 统一计费？
2. **`bridgeMode: currentTab` vs `newTabWithUrl`？** —— `currentTab` = 路飞自己打开页面后触发，最贴近 azan 的"Manus = AI 在博主上下文里工作"；`newTabWithUrl` = 全自动但更像爬虫。**推荐 currentTab**。
3. **Midscene 扩展是否随极光客户端一起分发？** —— 把扩展打进极光安装包，避免博主单独装扩展店。
4. **缓存共享？** —— 同一博主多设备时，`./midscene_run/cache` 是否同步到 wiki？建议**不同步**（每台设备分辨率/缩放可能不同）。
5. **Sidecar 还是 subprocess？** —— W2 用 subprocess（简单）；W5 看 50 条爆款的冷启动累积时间决定是否上 sidecar。

---

## 十二、与现有方案的关系

| 文档 | 关系 |
|---|---|
| [xhs-ai-only-team-design-review.md](plans/xhs-ai-only-team-design-review.md) | 编排底座，本文不变其结论 |
| [xhs-creator-ip-skus.md](plans/xhs-creator-ip-skus.md) | "拆爆款 fanout=50" 管线直接换数据源到 Midscene；SKU-A/C 不变 |
| [xhs-ingestion-replacement.md](plans/xhs-ingestion-replacement.md) | Tier 2"登录态 CDP 人速节流"获得**更强等价替代方案**；CDP 降级为 fallback |
| 当前 [plugins/xiaohongshu/tools.py 人速节流](plugins/xiaohongshu/tools.py) | 沿用 `_human_pace_check` / `_clamp_human_pace_*`，新路径并入同一闸门 |

---

## 十三、一句话结论

> **把"读取小红书页面"这件事，从"hermes Python 写 JS 注入到登录的 Chrome"切到"hermes 起一个 Midscene 子进程，让 VLM 看截图"**。指纹层面去掉 DOM 遍历 + 内部 API hit；语义层面把硬编码字段换成 prompt schema，抗 XHS 改版；成本层面 50 条爆款 ≤ ¥2.5（缓存命中后）。SKU "拆爆款 fanout=50" 是最大受益方。
