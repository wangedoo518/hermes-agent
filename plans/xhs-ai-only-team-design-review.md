# 小红书 AI-Only Team 产品 & 技术方案评审稿

> 评审目标：对照 https://kelvin.clockless.ai/ai-only-team?lang=zh 的「AI-Only Team」范式 + azan 提出的小红书博主赋能商业模式，评估 **hermes-agent 现有代码** 是否能承载该形态，并给出产品 / 技术落地方案与团队评审清单。
>
> 评审范围：当前 `main` 分支 hermes-agent 仓库 + 已存在的 `plugins/lufei_ai_team`、`plugins/xiaohongshu`、`plugins/tencentmeeting`、`plugins/teams_pipeline`、`hermes_cli/kanban*.py`、`skills/xhs-content-pipeline`。

---

## 一、对标分析：Clockless AI-Only Team vs. 当前仓库

| 维度 | Clockless（AI-only Team） | hermes-agent 当前实现 | 差距 |
|---|---|---|---|
| 角色编制 | Elon(CEO)/Jobs(Product)/Linus(Eng)/Turing(QA)/Bezos(CS) | Elon Mask(CEO)/Steve Jobs(产品)/Larry Page(检索)/Reed Hastings(增长内容)/Jeff Bezos(客户成功)/Satya Nadella(平台)/Sam Altman(质量门) ([scripts/lufei_ai_team.py:55-205](scripts/lufei_ai_team.py:55)) | 已 1:1 对齐且更细 |
| 协作介质 | 文件系统通信 + Clockless Engine 编排 | Hermes Kanban swarm（SQLite，blackboard 评论） + Profile/SOUL.md 文件系统 ([hermes_cli/kanban_swarm.py:77](hermes_cli/kanban_swarm.py:77)) | 已具备同构能力 |
| 拓扑 | CEO → 多 Agent 并行 → 验证 → 综合 | `create_swarm(workers, verifier, synthesizer)`：planner→并行 worker→verifier→synthesizer ([hermes_cli/kanban_swarm.py:77-180](hermes_cli/kanban_swarm.py:77)) | 完整覆盖 |
| 人类只读 dashboard | 「人只能看，不能操作」 | `plugins/kanban/dashboard/`（React 已构建 dist），plugin_api 暴露任务流 | 现成；只需把"操作按钮"关掉到只读模式 |
| 触发器 | 录音 → 30 min 对话起步 | `lufei_ai_team_orchestrate` 接收 WeChat/会议/语音/XHS URL ([plugins/lufei_ai_team/tools.py:19-65](plugins/lufei_ai_team/tools.py:19))；cron + webhook + gateway 全覆盖 ([hermes-already-has-routines.md](hermes-already-has-routines.md)) | 已具备 |
| 数据安全 | 未明确 | `xhs_init_lufei_wiki` 全本地 Obsidian llm-wiki；模型可走任意 provider（Nous/OpenRouter/本地） | 强于 Clockless |
| 内容工作流 | 通用软件交付 | XHS 专用：viral-analysis / topic-selection / script-generation / comment-intelligence / member-cs / service-diagnosis / quality-gate（10+ skill） | 远强于 Clockless |
| 商业模型 | 项目制 1/10 价格 | 未实现（需新建） | **本次主要待办** |

**结论**：azan 在 5/30 之前要跑通的「路飞案例」所需的底层全部已经存在；缺的是**产品包装层** —— 「数字博主交付包」 + 「极光（本地客户端）」 + 「分润 / CRM 计量」。

---

## 二、产品设计

### 2.1 核心定位

**「数字博主操作系统」**：把一位中部小红书知识付费博主的全部 SOP（拆解、选题、逐字稿、客服、CRM、复盘）封装成一台可本地部署的 AI-Only Team，博主继续收服务费，hermes-agent 团队收**铲子费 + 分润**。

对位 azan 会议记录中的策略：
- **卖铲子，不卖内容**：博主仍然是 IP 主体，AI 只接管 90% 琐事。
- **挑成熟博主**：要求博主已沉淀知识库（Obsidian / 飞书 / 微信收藏） → 复用 `xhs_init_lufei_wiki` + `xhs_ingest_account_to_wiki` 入库。
- **Manus 模式**：AI 在博主完整知识/上下文里工作，而非问答 → 等价于 hermes 的 profile + SOUL.md + skill + wiki_path 注入。
- **极光本地部署**：不采数据 → 直接复用 hermes 的 local-first + 任意 provider。

### 2.2 三层产品形态

| 层 | 名字 | 形态 | 复用现有代码 |
|---|---|---|---|
| L1 交付包 | **数字\<博主名\>** (e.g. 数字路飞) | 7 个角色 + N 个专属 skill + 1 个 wiki + 1 个 kanban board | `lufei_ai_team` 全套（已存在） |
| L2 客户端 | **极光 (Aurora)** | 博主本地 macOS/Windows 应用，封 hermes CLI + Kanban dashboard 只读视图 + Tray 通知 | hermes 二进制 + `plugins/kanban/dashboard` + `gateway` 多平台投递 |
| L3 SaaS 控制台 | **铲子工坊** | 我方运营后台：博主列表、分润计量、版本 / skill 推送 | **新建**（见 §3.5） |

### 2.3 用户旅程（博主侧）

1. **30 分钟腾讯会议** → `tmeet_export_records_to_obsidian` 自动落盘录音 / 字幕 ([plugins/tencentmeeting](plugins/tencentmeeting))。
2. **2 天搭建期** → 我方 ops 用 `lufei_ai_team_orchestrate(input=会议纪要)` 自动生成 7 profile + skill + kanban 初始板 + wiki 骨架。
3. **博主装极光客户端** → 本机起 hermes + gateway，绑定微信 / 小红书 cookie / 邮箱。
4. **稳态运转**：
   - 博主用微信发链接 / 文字 → gateway → CEO(Elon) → swarm → Reed/Larry/Jobs 出活 → Altman 质量门 → 综合稿回微信。
   - 客户咨询走 Bezos profile + xhs comment intelligence。
   - 评论 / 反馈 cron 每 4h 扫一遍（对位 Clockless"4 小时扫 backlog"）。
5. **博主审稿** → 极光只读看板看进度 / 评论；**只在 Altman 标 gate=pass 之后**才走人手最终决策。

### 2.4 商业模型计量点（决定哪里需要埋点）

- **服务费**：博主 → 客户（我方不入账）。
- **铲子费**：我方 → 博主，按数字员工套餐订阅。
- **分润 50%**：azan 5/30 提到的"兼职合伙人"。需要追踪：① 每条客户咨询的 AI 处理深度（comment 数）；② 转化为付费学员的事件回流。
- **评估方式**：azan 拍板「前期人工反馈推进」 → 不上数据采集；用 kanban verifier `gate` 通过率 + 博主在极光里的「👍 / 👎 / 重做」按钮作 proxy。

---

## 三、技术方案

### 3.1 复用 vs 新建一览

| 模块 | 状态 | 文件 |
|---|---|---|
| 7 角色 profile + SOUL 渲染 | ✅ 已实现 | [scripts/lufei_ai_team.py:55-227](scripts/lufei_ai_team.py:55) |
| Kanban swarm（fan-out / verifier / synthesizer） | ✅ 已实现 | [hermes_cli/kanban_swarm.py:77](hermes_cli/kanban_swarm.py:77), [hermes_cli/kanban.py:1329](hermes_cli/kanban.py:1329) |
| 6 类任务模板（xhs-content / interview / resume / portfolio / feedback / customer-consultation） | ✅ 已实现 | [scripts/lufei_ai_team.py:222-296](scripts/lufei_ai_team.py:222) |
| XHS 抽取 / 入库 / 检索 | ✅ 已实现 | [plugins/xiaohongshu/tools.py](plugins/xiaohongshu/tools.py) |
| 腾讯会议 / 网易云课堂 ingest | ✅ 已实现 | [plugins/tencentmeeting](plugins/tencentmeeting), [scripts/youdao_ingest_xhs_scripts.py](scripts/youdao_ingest_xhs_scripts.py) |
| Profile 间技能注入 | ✅ 已实现 | [scripts/lufei_ai_team.py:319-374](scripts/lufei_ai_team.py:319) (`sync_profile_skills`) |
| Cron / webhook / gateway 触发 | ✅ 已实现 | [cron/](cron), [gateway/](gateway), [hermes-already-has-routines.md](hermes-already-has-routines.md) |
| Kanban 只读 dashboard | 🟡 部分（dashboard 已存在，需关交互） | [plugins/kanban/dashboard](plugins/kanban/dashboard) |
| 极光 (Aurora) 桌面壳 | 🔴 新建 | `tui_gateway/` 已有 TUI，可作起点；客户端需用 Tauri 包 hermes 二进制 |
| 铲子工坊 SaaS 控制台 | 🔴 新建 | — |
| 分润计量 / 客户事件回流 | 🔴 新建 | — |
| 博主反馈（👍/👎/重做） | 🔴 新建 | 接到 kanban comment 即可，存元数据 |

### 3.2 端到端数据流（小红书爆款 → 逐字稿）

```
微信链接(博主)
  └─→ gateway/platforms/wechat → CEO(Elon) profile
        └─→ tool: lufei_ai_team_orchestrate(input=url)
              ├─→ classify -> "xhs-content"
              ├─→ swarm_command(): 创建 1 planner + 3 worker + verifier + synthesizer
              │     planner(Elon)            ─ blackboard 写 goal
              │     worker(Larry Page)        ─ xhs_extract_note / xhs_ingest_note_to_wiki
              │     worker(Reed Hastings)     ─ xhs-viral-analysis + xhs-topic-selection + xhs-script-generation
              │     worker(Steve Jobs)        ─ 服务/体验落地
              │     verifier(Sam Altman)      ─ lufei-quality-gate → metadata.gate=pass
              │     synthesizer(Elon)         ─ 拼装最终逐字稿
              └─→ kanban dispatcher 把 ready 卡发给对应 profile（独立进程 / Modal / Daytona / Vercel Sandbox）
                    每个 worker 写 wiki + kanban comment
                          ↓
                    极光客户端只读 dashboard 流式显示
                          ↓
                    Synthesizer 完成 → gateway 把最终稿回微信博主
```

azan 路飞案例「数小时压到半小时」的目标 = **3 个 worker 并行 + 大模型直出逐字稿**，时间瓶颈≈最慢的 worker（Reed Hastings 写稿），已可达。

### 3.3 关键技术决策

1. **不引入新编排引擎**：Kanban swarm + SOUL.md 已等价于 Clockless Engine。新增 Clockless-Engine-like 抽象 = 自找麻烦。
2. **每博主一个 tenant**：`hermes kanban swarm --tenant lufei` 已有 ([scripts/lufei_ai_team.py:480](scripts/lufei_ai_team.py:480))。SaaS 控制台按 tenant 计费。
3. **每博主一个独立 wiki 路径**：`DEFAULT_WIKI_PATH` 已参数化。极光客户端首次启动时生成 `~/Aurora/<creator_id>/wiki`，并以 `HERMES_HOME=~/Aurora/<creator_id>/.hermes` 隔离。
4. **provider 默认走本地 / Nous Portal**：满足 azan 「不采数据」原则。
5. **质量门是商业护城河**：`lufei-quality-gate` skill 决定输出可否到博主面前，必须有「拒绝-重做循环」对位 Clockless 的 Turing pattern。当前 `verifier` 已支持 `block + missing work`，但要补：① block 后自动重派同一 worker；② 重做次数上限（防止失控）。
6. **极光客户端：先 TUI 再 GUI**：`tui_gateway/` + `ui-tui/` 已是完整 TUI。短期内交付给博主的极光 v1 = `hermes` + 一个 Electron/Tauri 壳，嵌 `plugins/kanban/dashboard/dist/` 即可。
7. **博主反馈即数据**：kanban comment + `metadata.feedback={up,down,redo,note}`。不上 PostHog/分析平台 → 守 azan「不采数据」承诺。

### 3.4 新建工作清单（按优先级）

**P0 — 5/30 跑通路飞 demo 必须的：**
1. `plugins/lufei_ai_team` 增加 `lufei_ai_team_redo` tool：根据 verifier block 自动重派。
2. `plugins/kanban/dashboard` 增加 `?mode=readonly` query string，隐藏拖拽 / 创建 / 删除按钮。
3. `scripts/lufei_ai_team.py` 增加 `xhs-content` 模板的「半小时 SLA」标注（`max_runtime_seconds`）并在 verifier 评论里展示真实耗时。
4. 在 `tools.py` `xhs_run_content_skill` 之上拼 `lufei_ai_team_orchestrate` 的 e2e 测试（fixtures 已在 `skills/xhs-content-pipeline/tests/fixtures`）。

**P1 — 复制给第二位博主：**
5. `scripts/aurora_init_creator.py`（新建）：输入 creator_id + 30min 录音 + Obsidian 路径，输出 `~/Aurora/<creator_id>/{wiki,.hermes,profiles}` + 7 个 profile 自动 sync。
6. 客户咨询的「安全首轮回复」模板国际化 + 多博主话术覆盖（在 `lufei-member-cs` skill 内）。
7. Bezos profile 增加 cron «每 4 小时扫一次评论 backlog»（直接 `hermes cron create`）。

**P2 — 商业闭环：**
8. **铲子工坊**控制台（Next.js + 复用 hermes `web/`）：博主列表、订阅、tenant 健康度、版本推送、👍/👎 统计。
9. 极光客户端 Tauri 壳：MVP = 嵌 dashboard + 浮窗通知 + 一个「向 Elon 提问」输入框。
10. 分润事件回流：博主在极光里手动标「这条客户付费了 → ¥X」 → 写 kanban comment + 我方 SaaS pull。

### 3.5 风险与待评审决议

| # | 风险 / 决议 | 评审需拍板 |
|---|---|---|
| R1 | Verifier 拒绝-重做循环没有上限 → 死循环风险 | 默认上限 3 次，超出 → 升级为「Elon 写卡片求人」 |
| R2 | 单博主 wiki 容量随时间膨胀 → 检索退化 | 7 天热路 + 长期归档；走 `xhs_query_wiki_context` 的 FTS 索引 |
| R3 | 「不采数据」与「评估 SLA」冲突 | 仅在博主本机统计 gate 通过率 + 时延；不回传我方 |
| R4 | Profile 漂移：博主修改 SOUL.md 后版本断层 | 极光增加 `profile-checkpoint`，每次升级前 zip 备份 |
| R5 | 微信 / 小红书 cookie 失效 → 客服中断 | gateway 已有 health check；增加「向博主 push 一条二维码」流程 |
| R6 | 高客单博主自定义需求无止境（ariel 担忧） | 复用 azan 拍板："前两周搭基建，后续客户自行进化" → 把所有定制收敛到 skill 文件，博主自己改 |
| R7 | 极光客户端跨平台 → macOS Gatekeeper / Windows Defender 误杀 | MVP 先内测分发；正式版走 Apple Developer ID + EV cert |
| R8 | 50% 分润如何执行 → 现金还是积分 | 评审决议 |

---

## 四、评审议程建议（90 分钟）

1. **15'** — azan 5/30 路飞 demo 演示（live 跑 `lufei_ai_team_orchestrate`）。
2. **15'** — ariel / 陶双：底层框架是否已经吃透（对位 azan 敲打「深挖底层」）。
3. **20'** — 商业闭环（铲子费 + 分润 + 兼职合伙人结构）。
4. **15'** — 风险表 R1-R8 逐条拍板。
5. **15'** — P0 任务认领与 deadline（建议在 6/6 之前完成 P0 全部 4 项）。
6. **10'** — 是否需要为第二位博主立 case（验证可复制性）。

---

## 五、一句话结论

> **hermes-agent 现仓库 = Clockless AI-Only Team 的"小红书特化超集"**：编排（Kanban swarm）、角色（7 profile）、技能（10+ XHS skill）、数据入口（XHS / 腾讯会议 / 网易云课堂）、本地优先、多 provider 全部就绪。剩下的 70% 工作不是写编排，而是 **「极光客户端 + 铲子工坊 + 分润计量」 三件商业化包装**。azan 的"卖铲子"路线在技术侧没有阻塞，可按 P0→P1→P2 节奏推进。
