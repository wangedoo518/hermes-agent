# 小红书内容获取的合规替代方案

> 触发事件：路飞账号收到「疑似使用三方工具或脚本自动浏览」预警（首次仅警告，再犯影响功能）。
> 同期事件：账号"数据中心权限"已开通 — **官方合规通道开门**。
> 本文目标：把当前 `plugins/xiaohongshu` 的高风险动作逐项替换为合规渠道，并保留商业产品（[SKU 文档](plans/xhs-creator-ip-skus.md)）所需的全部数据。

---

## 一、风险定级：当前哪些动作触发预警

| 动作 | 文件 | 风险信号 | 等级 |
|---|---|---|---|
| 登录态 CDP 打开小红书 | [tools.py:1813](plugins/xiaohongshu/tools.py:1813) `_cdp_http_base_url` | 账号 + 自动化指纹 | **极高** |
| 全量主页 49/49 | [tools.py](plugins/xiaohongshu/tools.py) `xhs_extract_profile_notes` | 短时间 N 次内页跳转 | **极高** |
| `scroll_rounds=100` 默认 | tools.py:129 schema | 异常滚动深度 | **极高** |
| `max_comments=1000` 默认 | tools.py:95 schema | 异常深读评论 | 高 |
| 视频下载 + 转写 | tools.py:64 `download_video` | 短时间大流量 | 高 |
| OCR 默认开 | tools.py:55 `ocr` | 多图加载 + 来回请求 | 中 |
| 评论展开回复 | tools.py | 与真人行为差异大 | 高 |
| 调 `edith.xiaohongshu.com/api/sns/web/v2/comment/page` | tools.py:1675 | 直调内部 API | 极高 |

**判断**：警告几乎肯定由「登录态 + CDP + 主页全量 + 高频滚动」组合触发。**继续按当前节奏跑，下一次警告就会是限流 / 限发 / 封号**。

---

## 二、替代渠道全景

按"对账号风险"从低到高排序：

### Tier 0 — 完全零风控（官方授权 / 离线 / 用户主动）

| 渠道 | 数据 | 实施 |
|---|---|---|
| **官方数据中心**（截图开通的） | 博主自己账号的笔记列表、表现、粉丝、官方诊断 | 创作者中心 → 数据中心 → 每模块**导出 Excel/CSV** ([帆软 wiki](https://www.fanruan.com/finepedia/article/68ae9ee50bd240a239619dc8)) |
| **小红书开放平台**（白名单） | 自家 + 竞品笔记结构化数据、互动数据 | [open.xiaohongshu.com](https://open.xiaohongshu.com/document/developer/file/4)；2026 进入白名单+接入时代 ([阿里云 article](https://developer.aliyun.com/article/1722606)) |
| **用户主动分享/粘贴** | 单条笔记全文 + 截图 | 极光客户端"投喂"输入框 — 路飞自己点"分享 → 复制链接"再发给 AI |
| **博主截屏 + OCR**（本机） | 任意页面 | hermes 已有 vision tool；让博主 iOS 截屏 → AirDrop → OCR |
| **腾讯会议 / 网易云课堂 字幕** | 课程内容 | 已实现：`tmeet_export_records_to_obsidian`、`youdao_ingest_xhs_scripts` |
| **微信群聊语料** | 学员问答 | wecom 平台已实现 |
| **B 站行业课程**（公开 SSR） | 设计 / 求职课程 | 新建 `scripts/bilibili_ingest.py`，走 yt-dlp / SSR，不挂账号 |

### Tier 1 — 低风控（匿名公开页）

| 渠道 | 数据 | 实施 |
|---|---|---|
| **未登录 SSR HTML** | 单笔记基本字段（标题、正文、封面、可见统计） | 当前 `xhs_extract_note` 已有 SSR fallback ([tools.py:111](plugins/xiaohongshu/tools.py:111))；强制走匿名 httpx，不挂 cookie |
| **m.xiaohongshu.com 移动 SSR** | 同上 | 同上 |
| **xhslink.com 短链解析** | 笔记 ID | 已实现 |
| **RSSHub 自托管** | 博主公开 feed（+ cookie 可拿全文） | 部署独立的 rsshub 实例（**不要用我方主账号 cookie**，用临时小号或不挂 cookie） ([顾佳凯 blog](https://blog.gujiakai.top/2024/09/xiaohongshu-rss-tips/)) |

### Tier 2 — 中风险（限频 + 指纹分散）

| 渠道 | 数据 | 实施 |
|---|---|---|
| **登录态 CDP 但人速节流** | 评论、私域数据 | ✅ **已落地** ([plugins/xiaohongshu/tools.py](plugins/xiaohongshu/tools.py))：jitter 15-60s、scroll_rounds ≤ 5、max_comments ≤ 50、5 笔记/小时滚动窗口、`extract_comments` 默认 False、`HERMES_XHS_DANGEROUS_MODE=1` 显式绕开 |
| **指纹浏览器 + 住宅代理** | 多账号矩阵 | 不为当前 SKU 引入 — 风险高、成本高、平台明令打击（[腾讯云](https://cloud.tencent.com/developer/article/2580675)） |

### Tier 3 — 不再使用（红线）

| 渠道 | 原因 |
|---|---|
| 主账号 49/49 主页全量 | 直接触发预警 |
| 主账号直调 `edith.xiaohongshu.com/api/sns/...` | API 反向，平台明确打击 |
| `MediaCrawler` 等开源爬虫挂主账号 | 同上 |

---

## 三、按数据需求 → 替代渠道映射

照搬 SKU 文档对数据的需求：

| 数据需求 | 旧方式（高风险） | **新方式** |
|---|---|---|
| 路飞自己笔记的表现数据 | `xhs_extract_profile_notes` 全量 | **数据中心 Excel 导出**（手动 / 半自动 CDP 仅在 creator 子域） |
| 路飞自己单条笔记的评论 | CDP DOM scroll 1000 条 | 数据中心 + **学员私聊里复制粘贴** + 评论用 RSSHub 拉公开评论 |
| 拆爆款（竞品笔记） | 同上 | **路飞手动分享笔记链接给 AI** → SSR 匿名抓 → AI 处理；50 条/天上限 |
| 行业知识库（B 站课程） | — | 新建 `bilibili_ingest.py` |
| 学员上下文（CRM） | 微信群语料 | wecom 已实现 ✓ |
| 内容生成的"灵感池" | 主页全量爬 | **数据中心导出 + 路飞自选 top 20 历史爆款** → 一次性入 wiki |

---

## 四、代码侧改造清单

### P0 — 24 小时内必做（止血）

1. ✅ **批量路径默认值已收紧** ([plugins/xiaohongshu/tools.py](plugins/xiaohongshu/tools.py))：
   - `xhs_extract_profile_notes`：`max_notes` 500→**3**（hard cap 5），`scroll_rounds` 100→**2**（hard cap 5）
   - `xhs_extract_note`：`max_comments` 1000→**30**（hard cap 50），`extract_comments` True→**False**
   - `_clamp_human_pace_*` 在所有内部入口强制裁剪（即使旧调用方传大值也不会生效）

2. ✅ **`HERMES_XHS_DANGEROUS_MODE` 显式绕开开关**（默认关）：
   - 关：jitter 15-60s + 5 笔记/小时滚动窗口 + 全部 hard cap 生效
   - 开：日志大喊「bypassed by HERMES_XHS_DANGEROUS_MODE=1」+ 恢复 1000/500/100 上限
   - 在 [tools.py `_xhs_dangerous_mode_enabled`](plugins/xiaohongshu/tools.py) 集中开关

3. **本机已有 59 个 note.json 不动，不要重抓**。把抓取目标改成"只抓博主主动投喂的 URL"。

### P1 — 一周内做完（替代渠道接入）

4. **新增 tool `xhs_creator_center_import`**：
   - 输入：Excel/CSV 路径（数据中心导出文件）。
   - 输出：解析为 note 表 + 表现表，写入 wiki，**完全不联网**。
   - 文件：`plugins/xiaohongshu/creator_center_import.py`（新建）。

5. **新增 tool `xhs_share_url_ingest`**：
   - 输入：单条用户分享的 URL（含 `xhslink.com` 短链）。
   - 强制走 SSR 匿名 httpx，不挂 cookie；不抓评论；不下载视频（让博主截图替代）。
   - 速率：单进程 ≤ 60 条/天，硬上限。

6. **删除 / 不再注册** `xhs_extract_profile_notes` schema（或仅在 `HERMES_XHS_DANGEROUS_MODE=1` 时注册）。

7. **`bilibili_ingest.py`**：替代行业知识库抓取，走 yt-dlp / 字幕 API，**不挂账号**。

### P2 — 一个月内做完（合规体系）

8. **接入小红书开放平台**（白名单申请走起）：
   - 注册 [open.xiaohongshu.com](https://open.xiaohongshu.com) 开发者账号。
   - 申请「内容数据接口」「商业数据接口」。
   - 新建 `plugins/xiaohongshu/openapi.py`，承担**所有竞品分析需求**。
   - 拿到接口后，删除全部 SSR 抓取，只保留 share-url-ingest 作 fallback。

9. **自托管 RSSHub 实例**（在云电脑里跑）：
   - docker 起 RSSHub，做博主"自己关注的人"的 feed 监控。
   - **不挂主账号 cookie**，挂一个独立小号 cookie 或匿名。
   - 用作 SKU-B（会员问答）的实时资讯来源。

10. **数据中心半自动桥**：
    - 创作者中心是另一个子域（creator.xiaohongshu.com），风控宽松得多。
    - 写一个**只在 creator 子域**生效的 CDP 脚本：定时点"数据中心 → 导出 Excel" → 下载到本机 → 自动 `xhs_creator_center_import`。
    - 此举把"读自己的数据"从用户感知的爬虫降级为"自动化点导出按钮"，平台层面合规。

---

## 五、对 SKU 文档的影响

| SKU | 是否受影响 | 调整 |
|---|---|---|
| SKU-A 模拟面试 | ❌ 不依赖 XHS 实时抓取 | 无 |
| SKU-B 会员问答 | 🟡 实时资讯依赖 | 改用 RSSHub + 数据中心导出 |
| SKU-C Web Coding 课 | ❌ 不依赖 | 无 |
| 拆爆款 fanout=50 内容管线 | 🔴 强依赖 | **核心改造**：从"主页全量爬" → "路飞分享 50 个 URL 给 AI"，加上数据中心历史爆款导入 |

**新 SOP（azan 风格）**：
> 路飞晚上花 5 分钟，把当天看到的 10-20 条爆款分享到企业微信群 → 极光在群里识别 URL → SSR 匿名抓 → 第二天早上 fanout=50 出脚本候选。

这条 SOP 比"自动爬主页"**更接近 azan 的 Manus 模式** —— 路飞本来就在刷小红书，分享一下零成本。

---

## 六、风险残留 & 待 azan 决策

1. **路飞自己的账号"读自己"是否需要保护？** —— 数据中心导出可以，但如果还想监听评论实时，最好用「**另一个小号**」+ RSSHub。问 azan 是否能给个小号。
2. **开放平台白名单申请耗时**（业内 2-4 周）— 在拿到之前 SKU-A/B 都靠手动 + Tier 0/1 渠道，足够跑 W1-W4。
3. **数据中心 Excel 模式有滞后**（通常昨日数据）— 对 SKU-B 实时性影响 < 1 天，可接受。
4. **删 `xhs_extract_profile_notes` 是否影响其它已有任务模板？** — 已检查：`xhs-content` 模板用的是 `xhs_extract_note` 单条入口，不受影响；`feedback-backlog` 评论扫描需要降级到「路飞主动复制」。

---

## 七、行动清单（按时间）

| 时间 | 任务 | Owner |
|---|---|---|
| **今天** | 关 SAFE_MODE 开关、关全量主页、停止后台 cron | 我方 ops |
| **今天** | 给路飞发指南：以后给 AI 投喂用"分享链接"按钮 | 我方 + 路飞 |
| **本周** | 实现 `xhs_creator_center_import` + `xhs_share_url_ingest` | 我方 |
| **本周** | 申请开放平台 / 蒲公英开发者账号 | 路飞（IP 主体提交） |
| **下周** | 部署自托管 RSSHub（云电脑里） | 我方 |
| **第三周** | 半自动数据中心 Excel 定时导出 | 我方 |
| **W3-W6** | 开放平台白名单审批 → 接入 | 我方 |

---

## Sources

- [小红书开放平台开发者文档](https://open.xiaohongshu.com/document/developer/file/4)
- [创作者数据中心数据导出（帆软 wiki）](https://www.fanruan.com/finepedia/article/68ae9ee50bd240a239619dc8)
- [小红书 API+AI 2026 趋势（阿里云）](https://developer.aliyun.com/article/1722606)
- [小红书 RSS 解决方案（顾佳凯 blog）](https://blog.gujiakai.top/2024/09/xiaohongshu-rss-tips/)
- [小红书账号风控与指纹浏览器（腾讯云）](https://cloud.tencent.com/developer/article/2580675)
