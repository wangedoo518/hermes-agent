# 路飞知识水电站 AI Team + Hermes Kanban 落地计划

日期：2026-05-23

## 目标

把路飞设计沉思录从“单个 Hermes 助手”升级成一个可调度的 AI 运营团队：

1. 路飞个人微信 DM 是内部运营入口。
2. 未来客户微信客服入口只做需求采集、资料领取、预约引导和低风险答疑。
3. Hermes Kanban 是唯一任务编排引擎。
4. 已有小红书链路（提取、爆款拆解、选题、逐字稿）必须进入 Kanban。
5. 面试复盘、简历点评、作品集点评作为第一阶段的内部助教工作流。
6. 暂不自动发布小红书，暂不自动交付收费 SKU。

## AI Team

| Profile | 内部代号 | 职责 |
| --- | --- | --- |
| `lufei-ceo` | Elon Mask | 提取 intent，创建 Kanban，定义验收标准，最终综合 |
| `lufei-jobs` | Steve Jobs | 服务流程、诊断模板、内容包装、SKU 原型 |
| `lufei-page` | Larry Page | 搜索、采集、小红书、会议、课程和 wiki 入库 |
| `lufei-hastings` | Reed Hastings | 爆款拆解、选题、逐字稿、内容增长 |
| `lufei-bezos` | Jeff Bezos | 微信客服、CRM、客户反馈、backlog |
| `lufei-nadella` | Satya Nadella | Hermes/Obsidian/微信/tmeet/XHS 系统集成 |
| `lufei-altman` | Sam Altman | 引用、置信度、人设、隐私和幻觉校验 |

## 分步实施与自测

### Step 1. 补齐路飞 IP 总档案

实现：

- 更新 `lufei-xhs-wiki/entities/lufei.md`。
- 写入履历、服务 SKU、店铺数据、群聊、直播、AI 交付边界。

自测：

```bash
test -f /Users/champion/Documents/develop/Wiki/ClaudeWiki/lufei-xhs-wiki/entities/lufei.md
rg "9732|1V1 体验咨询|Elon Mask|不先做完整模拟面试机器人" \
  /Users/champion/Documents/develop/Wiki/ClaudeWiki/lufei-xhs-wiki/entities/lufei.md
```

### Step 2. 建立三条诊断工作流和小红书内容引擎概念页

实现：

- `concepts/interview-debrief-flow.md`
- `concepts/resume-review-flow.md`
- `concepts/portfolio-review-flow.md`
- `concepts/xhs-content-engine.md`
- `queries/kanban-task-templates.md`

自测：

```bash
for f in \
  concepts/interview-debrief-flow.md \
  concepts/resume-review-flow.md \
  concepts/portfolio-review-flow.md \
  concepts/xhs-content-engine.md \
  queries/kanban-task-templates.md; do
  test -f "/Users/champion/Documents/develop/Wiki/ClaudeWiki/lufei-xhs-wiki/$f"
done
```

### Step 3. 新增 AI Team bootstrap 脚本

实现：

- `scripts/lufei_ai_team.py`
- 支持 `doctor`、`setup-profiles`、`render-soul`、`render-task`、`render-swarm-command`、`create-blocked-seed-task`。

自测：

```bash
python -m py_compile scripts/lufei_ai_team.py
python scripts/lufei_ai_team.py doctor
python scripts/lufei_ai_team.py render-swarm-command xhs-content \
  --source "https://www.xiaohongshu.com/explore/test"
```

### Step 4. 创建 Hermes Profiles

实现：

- 通过 `hermes profile create --clone --description` 创建缺失 profile。
- 写入每个 profile 的 `SOUL.md`。

自测：

```bash
python scripts/lufei_ai_team.py setup-profiles --force-soul
hermes profile list
for p in lufei-ceo lufei-jobs lufei-page lufei-hastings lufei-bezos lufei-nadella lufei-altman; do
  test -f "$HOME/.hermes/profiles/$p/SOUL.md"
done
```

### Step 5. 接入 Kanban 任务模板

实现：

- 小红书内容生产任务：`xhs-content`
- 面试复盘任务：`interview-debrief`
- 简历点评任务：`resume-review`
- 作品集点评任务：`portfolio-review`
- 反馈 backlog 任务：`feedback-backlog`

安全自测：

```bash
python scripts/lufei_ai_team.py create-blocked-seed-task xhs-content \
  --source "测试：小红书爆款拆解链路" \
  --dry-run
```

实际创建安全种子任务：

```bash
python scripts/lufei_ai_team.py create-blocked-seed-task xhs-content \
  --source "测试：小红书爆款拆解链路"
hermes kanban list --tenant lufei
```

脚本会在创建后再次调用 `hermes kanban block`，确保种子任务停在 `blocked` 状态，不会被 gateway dispatcher 自动执行。

### Step 6. 单测

实现：

- 新增 `tests/scripts/test_lufei_ai_team.py`。

自测：

```bash
python -m pytest -o addopts='' tests/scripts/test_lufei_ai_team.py -q
```

## 后续接入

1. 微信个人号 DM 收到路飞指令后，由 `lufei-ceo` 创建 Kanban。
2. 客户微信客服入口进入 `lufei-bezos`，只做需求采集、CRM 建档和预约引导。
3. `lufei-altman` 作为 Turing 位质量门，控制正式 wiki 写入。
4. 收费 SKU 等内部助教链路稳定后再设计，不在第一阶段承诺。
