# 路飞 IP 产品化 v1：SKU 清单 + 90 天交付路线

> 来源：azan 三场会议（IP 产品化战略 / 模拟面试机器人 & 会员咨询 / 企业微信 AI 答疑）。
> 配套：[plans/xhs-ai-only-team-design-review.md](plans/xhs-ai-only-team-design-review.md)（编排底座，已就绪）。
> 目标：把 azan 的「卖铲子 + 按效果分成」战略，拆成两个能在 **2 周内变现** 的 SKU 和一条 **90 天复制** 路线。

---

## 一、本轮新增的产品决议（vs 上轮）

| 决议 | 出处 | 落地点 |
|---|---|---|
| 砍掉「等数字员工跑通再变现」，先卖现成 SKU | azan 12:57、15:21 | SKU-A、SKU-B 见 §二 |
| 主交付物：**模拟面试机器人 ¥999** | azan 12:57 | SKU-A |
| 抓手：**专业咨询会员 ¥39/月 / ¥1199/年** | azan 13:30 | SKU-B |
| 第三个产品：**Web Coding 课**（一周搭一周卖，五五分成） | azan 17:17 | SKU-C（试水可复制性） |
| **按效果分成**取代一次性定制 | azan 13:30、14:27 | 计量见 §五 |
| 知识蒸馏：陆老师内容 + B站行业课程审核 → 不大概率正确 = 商业护城河 | azan 15:59 | §四质量门 |
| 内容生产：**多模型并行 10-100 版 → 人选** | 教育咨询纪要 | 复用 `kanban_decompose fanout=true` |
| 部署：**云电脑 + 企业微信群监听**（解本机环境问题） | 教育咨询纪要 | §六 部署形态 |
| 答案先到人，**人审核后再发学生** | 教育咨询纪要 | 复用 `/approve` 闸 |
| 微信中心化 + 语音指令 | IP 产品化纪要 | wecom 平台 + STT 已就绪 |

---

## 二、SKU 矩阵

### SKU-A：模拟面试机器人「陆老师 · AI 面试官」

| 项 | 设计 |
|---|---|
| 定价 | ¥999 / 次（一次 = 1 场面试 + 1 份复盘报告） |
| 用户旅程 | 学员付费 → 选岗位 / 公司 → AI 30 分钟问答（语音）→ AI 自动复盘 → 路飞抽样审核 → 推送给学员 |
| 替代什么 | 路飞 1 小时（30 分钟面试 + 30 分钟复盘） → 路飞抽样审核 5-10 分钟 |
| 模型协同 | 提问端：1 个 profile（codename：**HR-Agent**，新 profile）；复盘端：Sam Altman + Steve Jobs；评级端：Reed Hastings |
| 知识源 | `lufei-xhs-wiki/concepts/interview/*` + B站爬的行业面试合集（新增 `scripts/bilibili_ingest.py`） + 该学员 CRM 档案 |
| 质量门 | Altman 必须给出 ≥3 条引用 + confidence 标注；路飞拒绝重做（已有 verifier 拒重派） |
| 交付物 | `~/Aurora/<creator>/deliveries/interview/<student_id>/{transcript.md, report.md, audio.mp3}` |

落代码点（在已有仓库内）：
- 新 profile：`lufei-hr`（codename: Steve Wozniak / Susan Wojcicki，选一个 azan 偏好的）。
- 新任务模板：`TASK_TEMPLATES["mock-interview"]`，加在 [scripts/lufei_ai_team.py:222](scripts/lufei_ai_team.py:222) 后面。
- 复用 `interview-debrief` 模板的复盘部分。
- 新 skill：`skills/xhs-content-pipeline/skills/lufei-mock-interviewer/SKILL.md`。

### SKU-B：专业咨询会员「陆老师 · 微信问答」

| 项 | 设计 |
|---|---|
| 定价 | ¥39/月（试水）或 ¥1199/年（深度） |
| 渠道 | 企业微信群 + 1 对 1 微信 |
| 流程 | 学员发问 → wecom_callback 触发 → CEO(Elon) 分类 → Bezos(客户成功) 起草 → Altman 质量门 → **路飞 `/approve` 审核** → wecom 回学员 |
| AI 处理上限 | 高频 / 标准化问题（已有 `customer-consultation` 模板，[scripts/lufei_ai_team.py:282](scripts/lufei_ai_team.py:282)） |
| 升级钩子 | 复杂 / 高价值问题 → AI 引导 → 模拟面试 / 1v1 咨询付费转化（azan 15:41：「留个钩子嘛」） |
| 知识源 | 三源合并：① 视频字幕（网易云课堂、B站）；② 小红书定期爬（cron）；③ 历史微信群聊语料 |

落代码点：
- 复用 wecom 平台 + `customer-consultation` 模板。
- 新增 cron job：每天 02:00 爬目标博主小红书 → `xhs_ingest_account_to_wiki` → wiki 自动更新。
- 复用 `pairing.py` 做学员会员状态校验（pairing 已有 lockout + 配额，可加 entitlement 字段）。
- **新 tool**：`xhs_membership_check(user_id, level)` 返回会员级别，决定 AI 答多少、何时挂钩子升级。

### SKU-C：Web Coding 速成课（最小验证可复制性）

| 项 | 设计 |
|---|---|
| 定价 | ¥1000 × 10 节 |
| 制作周期 | 1 周搭课 + 1 周卖 |
| 我方收益 | 与路飞 50/50 分成 |
| 用途 | **验证**「数字博主一次搭建→多次复制」可行性，为 SKU-A/B 复制到穿搭 / 手串等垂类做先导 |

落代码点：
- 复用 SKU-A 全部基础设施。
- 增加 `scripts/lufei_curriculum_generate.py`：输入大纲 → fanout 出 10 节课的逐字稿 + 配套作业 + PPT 文本。

---

## 三、内容生产管线（azan 主要兴趣点）

azan 18:40：「单视频 3 小时 → 我直接 50 个挑 10 个」。

**直接对位 `kanban_decompose fanout=true`**：

```
微信 / 语音指令： "拆这个爆款 https://xhslink.com/..."
   ↓ wecom -> CEO(Elon) profile
   ↓ tool: lufei_ai_team_orchestrate(input=url)  ← 路由到 xhs-content
   ↓ swarm 创建：Larry Page 抓 → Reed Hastings 拆 → fanout=50 个脚本草稿
   ↓ kanban_decompose 把 "写 50 条候选脚本" 切成 50 张子卡，并发跑
   ↓ 50 张卡分别用 不同 model（豆包 / Gemini / GLM / Kimi / Nous）—— /model 已支持
   ↓ Sam Altman 质量门粗筛剩 20 张
   ↓ Steve Jobs 按情绪曲线再筛 10 张
   ↓ 综合输出到极光看板：人挑 1 张拍板
```

**多模型并行的实现细节**：
- `kanban_decompose` 已支持 `fanout=true` 一拆 N（[hermes_cli/kanban_decompose.py:134](hermes_cli/kanban_decompose.py:134)）。
- 给每张子卡通过 `--worker reed-hastings:<title>:<skills>` 指定不同 profile；每个 profile 在 `profile.yaml` 里配不同的 `provider:model` —— 就实现了「同一题 50 个模型并行写」。
- 把 model 池作为「投票池」存 `~/Aurora/<creator>/model-pool.yaml`，10 个 model id 一行。

落代码点：
- 新增 `skills/xhs-content-pipeline/skills/xhs-script-fanout-50/SKILL.md`：固化 azan 的「拆 50 选 10」SOP。
- `scripts/lufei_ai_team.py` 增加 `xhs-content-fanout` 任务模板，把 fanout N 作为参数。

---

## 四、知识蒸馏与质量门（护城河）

azan 15:59 / 17:17 拍板：**蒸馏 = 商业壁垒**。

| 知识源 | 入库方式 | 状态 |
|---|---|---|
| 路飞历史咨询录音 / 字幕 | `tmeet_export_records_to_obsidian` ([plugins/tencentmeeting](plugins/tencentmeeting)) | ✅ |
| 路飞小红书 / 抖音 / B 站视频 | `xhs_ingest_account_to_wiki` + 新增 `bilibili_ingest.py`、`douyin_ingest.py` | 🟡 XHS ✅，B 站抖音待补 |
| 行业 B 站课程 | `bilibili_ingest.py` 新建 | 🔴 |
| 学员历史微信聊天记录 | wecom 群消息归档已有 | ✅ |
| 优 / 劣面经案例库 | azan 待办「提供一批面经链接」→ 入 `~/Aurora/<creator>/wiki/interview-cases/{good,bad}/` | 🔴 数据待 azan 提供 |

**质量门关键规则**（写进 `lufei-quality-gate` skill）：
1. AI 任何「事实判断」必须给 ≥1 条 wiki 引用（已有，确认覆盖率）。
2. 「面试评级 / 简历评分」必须给 ≥3 条**正面案例 + 反面案例**对比。
3. 跨赛道（穿搭、手串）回答时强制改用边界提示：「这是路飞的设计方法论迁移版，仅供参考」。
4. 重做次数上限 3，超出 → 升级人。
5. 命中「价格 / offer / 包过」类承诺词 → 一票否决。

---

## 五、按效果分成的计量

azan 13:30：「客户营收带起来，分个点」。

| 事件 | 触发 | 存储 |
|---|---|---|
| `interview.delivered` | SKU-A 学员收到复盘 | kanban comment + metadata.sku="A" |
| `consult.answered` | SKU-B 单条回复 `/approve` 通过 | metadata.sku="B" |
| `consult.upsell_clicked` | 学员点钩子升级 | metadata.upsell=true |
| `course.sold` | SKU-C 一节课售出 | 人工录入极光「卖出」按钮 → kanban comment |
| `revenue.attributed` | 博主月底回填本月营收 | 极光 monthly journal |

**不传我方服务器**（守 azan「不采数据」承诺）：
- 所有事件都在博主本机 SQLite。
- 月底博主点「同步分润」→ 极光仅上传 **聚合数 + 哈希**（不上原文）到铲子工坊。
- 异议时博主提供本机 csv 对账。

落代码点：
- 新建 `plugins/aurora_billing/`：单 plugin，定义 `aurora_attribute_event`、`aurora_monthly_report` 两个 tool。
- 复用 `cron`：每月 1 号自动生成上月报表草稿。

---

## 六、部署形态：云电脑 + 极光本地双轨

教育咨询纪要明确：「计划使用云电脑部署 AI 助手，解决本地环境兼容性问题」。

**两套部署模式并存**，按博主选择：

### 模式 1：极光本地版（Aurora Local）

- macOS / Windows 客户端，全本地 hermes，最强隐私。
- 适合：路飞这种已有完整知识库 + 在意数据安全的中部博主。
- 路径：用户 → Tauri 壳 → 本机 hermes daemon → kanban → wecom 个人微信号。

### 模式 2：云电脑版（Aurora Cloud）— 教育咨询纪要的方案

- 阿里云 / 腾讯云 ECS（或 Daytona / Modal serverless，hermes 已原生支持）。
- 部署 hermes-gateway，登录企业微信，监听指定群聊。
- 适合：怕本机 24h 开机 / 微信封号风险敏感的博主。
- 成本测算（azan 待办）：
  - ECS：阿里云 4C8G ≈ ¥300/月（含云电脑授权）
  - Token：路飞日均咨询量 × 平均 token × 模型单价 → 走 Nous Portal 包月最划算，建议 ¥500/月起测。
  - 总成本目标：单博主 < ¥1000/月（vs SKU-A 单笔 ¥999，模拟面试卖出 1 单即回本）。

落代码点：
- 复用 hermes 现有的 Daytona / Modal serverless backend（README 提到了）。
- 新增 `docker/aurora-cloud.Dockerfile`：一键起 gateway + kanban + wecom。
- 新增 `scripts/aurora_cloud_bootstrap.sh`：阿里云 / 腾讯云 cloud-init 脚本。
- 企业微信群监听已在 [plugins/.../wecom_callback.py](gateway/platforms/wecom_callback.py)，要确认能在群消息里识别 @机器人。

---

## 七、90 天里程碑（建议）

| 周次 | 里程碑 | 验收 |
|---|---|---|
| **W1**（5/24-5/30）| SKU-A MVP：HR-Agent profile + mock-interview 模板 + 一次真实学员压测 | 路飞拿到一份 AI 生成的复盘，亲自打分 ≥7/10 |
| W2 | SKU-A 上微信群售卖，跑 5 单 | 现金回笼 ≥¥3000 |
| W3 | SKU-B MVP：wecom + customer-consultation + `/approve` 回环 | 路飞日均审核 ≤30 分钟回完当天群内所有咨询 |
| W4-W5 | SKU-B 上线月费 ¥39 + 年费 ¥1199 | 首批 30 个会员 |
| W6 | **拆爆款 fanout=50** 内容管线交付 | 路飞日更小红书 ≥3 条，AI 拆解时间 < 0.5h |
| W7-W8 | Aurora Cloud 部署形态跑通 | 一台阿里云 ECS 跑路飞全天 zero-touch |
| W9 | **SKU-C Web Coding 课**搭建 | 1 周内完成 10 节课文本 + PPT |
| W10 | SKU-C 卖出 ≥¥30000 | 50/50 分成入账 |
| W11-W12 | 复制到第 2 位博主（穿搭 or 手串） | 用 `aurora_init_creator.py` 30 分钟内开服 |
| W13 | 铲子工坊 v1（运营后台）上线 | 双博主统一面板 |

---

## 八、与上轮文档的关系

| 上轮 P0 | 本轮覆盖 |
|---|---|
| dashboard `?mode=readonly` | 仍是 W1 必做 |
| verifier 自动重派 + 上限 | 仍是 W1 必做（SKU-A 质量门强依赖） |
| `xhs-content` SLA 标注 | 移到 W6（fanout 一起做） |
| e2e 测试 | W2 覆盖 SKU-A 路径 |

上轮 P1/P2 中：
- **P1-5（aurora_init_creator）** 提到 W11。
- **P2-8（铲子工坊）** 提到 W13。
- **P2-9（极光客户端壳）** —— 与 Aurora Cloud 形态二选一并存，W2 起按需。
- **P2-10（分润事件回流）** —— 本轮升级为 SKU 计量体系（§五），W3 起做。

---

## 九、留待团队拍板（5 条最关键）

1. **SKU-A 的 30 分钟面试是否完全 AI 主问？** azan 倾向「机器人模拟面试，路飞只做抽样审核」。若选 AI 主问，必须先给 5 场学员压测降幻觉。
2. **SKU-B 答案是否强制人审？** azan 倾向「先人工反馈推进」 = 强制。等 gate 通过率 ≥95% 后开放白名单自动回。
3. **云电脑 vs 极光本地**：W1 是否两条都做？建议先云电脑（解微信封号 + 24h 在线），本地版滞后到 W6。
4. **多模型 fanout 是否调用付费 API？** Nous Portal 包月 vs 各家 free tier 拼盘。建议 W1 直接走 Nous Portal 简化结算。
5. **分润哈希上传是否仍触犯「不采数据」承诺？** 若严格守，分润只能博主主动按月填表，无审计能力。需要 azan 拍板「弱审计可接受」。
