# 路飞设计沉思录小红书 AI 运营体系落地计划

日期：2026-05-22

## 目标

基于 Hermes 打通一条本地优先的小红书 AI 运营闭环：

1. `xhs_extract_note` 提取单条图文/视频笔记、图片 OCR、视频转写、评论、互动数据。
2. `xhs_extract_profile_notes` 提取账号主页笔记清单。
3. `xhs_init_lufei_wiki` 初始化路飞本地 llm-wiki，默认优先落在当前 Obsidian vault 下。
4. `xhs_ingest_note_to_wiki` / `xhs_ingest_account_to_wiki` 把笔记和账号清单沉淀到 `raw/`。
5. `xhs_build_wiki_manifest` 生成 `_derived/manifest.json`，作为下游技能的文件适配层。
6. `xhs_query_wiki_context` 按任务读取 wiki 上下文。
7. `xhs_run_content_skill` 调用本地 `xhs-content-pipeline`，串起爆款拆解、选题、逐字稿、评论情报。
8. `xhs_open_wiki_in_obsidian` 打开 Obsidian 中的 wiki，供人工复核和持续维护。

## 路径

- Hermes 插件：`plugins/xiaohongshu/`
- 内容技能仓库：`/Users/champion/Documents/develop/skills/xhs-content-pipeline`
- 默认 wiki：当前 Obsidian vault 下的 `lufei-xhs-wiki`
- 当前本机默认实际路径：`/Users/champion/Documents/develop/Wiki/ClaudeWiki/lufei-xhs-wiki`

## 分步实施与验收

### Step 1. 修复主页笔记清单解析

实现：

- 从 SSR HTML 的 `<a href="/explore/...">` 提取 `note_id` / `xsec_token`。
- 从 `window.__INITIAL_STATE__` 中提取标题、类型、嵌套 `interactInfo` 统计。
- 支持 Browser/CDP 已登录页面作为优先来源。

自测：

```bash
python -m pytest -o addopts='' tests/plugins/test_xiaohongshu_plugin.py -q
```

验收：`test_extracts_profile_notes_from_html_anchors_and_initial_state` 通过。

### Step 2. 建立 Obsidian/wiki 文件适配层

实现：

- `xhs_init_lufei_wiki` 创建 `raw/`、`entities/`、`concepts/`、`comparisons/`、`queries/`、`_derived/`。
- 若没有配置 `LUFEI_XHS_WIKI_PATH` / `XHS_WIKI_PATH` / `WIKI_PATH`，自动使用 `obsidian vault info=path` 的当前 vault。
- `xhs_open_wiki_in_obsidian` 通过 Obsidian CLI 打开 `index.md`。

自测：

```bash
obsidian --help
python - <<'PY'
from plugins.xiaohongshu import tools as xhs
result = xhs.init_lufei_wiki()
print(result["success"], result["obsidian"])
PY
```

验收：Obsidian CLI 可用，wiki 在 active vault 内，`inside_active_vault=true`。

### Step 3. 笔记与账号入库

实现：

- `xhs_ingest_note_to_wiki` 写入：
  - `raw/xhs/notes/<note_id>/note.json`
  - `raw/xhs/notes/<note_id>/note.md`
  - `raw/xhs/notes/<note_id>/transcript.md`
  - `raw/xhs/notes/<note_id>/comments.md`
- `xhs_ingest_account_to_wiki` 写入：
  - `raw/xhs/profile/<profile_id>/profile.json`
  - `raw/xhs/profile/<profile_id>/profile.md`
  - `queries/account-note-inventory.md`

自测：

```bash
python - <<'PY'
from pathlib import Path
from plugins.xiaohongshu import tools as xhs

wiki = xhs._resolve_wiki_path(None)
note_json = Path("/Users/champion/.hermes/cache/xiaohongshu/69da513a0000000023005dfa/note.json")
note_md = note_json.with_name("note.md")

xhs.init_lufei_wiki(wiki_path=str(wiki))
result = xhs.ingest_note_to_wiki(
    note_json_path=str(note_json),
    note_md_path=str(note_md),
    wiki_path=str(wiki),
)
print(result["success"], result["raw_note_md_path"])
PY
```

验收：缓存里的路飞 vol.13 视频笔记可被沉淀进 Obsidian wiki。

### Step 4. manifest 与 wiki context

实现：

- `xhs_build_wiki_manifest` 扫描 Markdown，生成 `_derived/manifest.json`。
- `xhs_query_wiki_context` 直接读取文件，不假装 llm-wiki 有事件 API。

自测：

```bash
python - <<'PY'
from plugins.xiaohongshu import tools as xhs
wiki = xhs._resolve_wiki_path(None)
manifest = xhs.build_wiki_manifest(wiki_path=str(wiki))
query = xhs.query_wiki_context(
    query="字节 UIUX 压力面 模拟面试",
    wiki_path=str(wiki),
    max_files=5,
)
print(manifest["file_count"], len(query["matches"]), query["matches"][0]["path"] if query["matches"] else "")
PY
```

验收：query 能命中 `raw/xhs/notes/69da513a0000000023005dfa/note.md`。

### Step 5. 内容 skill 桥接

实现：

- `xhs_run_content_skill` 作为 Hermes 到 `/Users/champion/Documents/develop/skills/xhs-content-pipeline/run_skill.py` 的桥。
- 支持 `viral-analysis`、`topic-selection`、`script-generation`、`comment-intelligence`。

自测：

```bash
python /Users/champion/Documents/develop/skills/xhs-content-pipeline/run_skill.py --help
python -m py_compile \
  /Users/champion/Documents/develop/skills/xhs-content-pipeline/run_skill.py \
  /Users/champion/Documents/develop/skills/xhs-content-pipeline/whisper_transcribe.py
python -m pytest -o addopts='' tests/plugins/test_xiaohongshu_plugin.py -q
```

验收：Hermes 插件单测覆盖本地 skill 调用桥接，脚本语法可用。

## 当前已通过自测

```bash
python -m py_compile plugins/xiaohongshu/tools.py plugins/xiaohongshu/__init__.py
python -m pytest -o addopts='' tests/plugins/test_xiaohongshu_plugin.py -q
# 30 passed

python /Users/champion/Documents/develop/skills/xhs-content-pipeline/run_skill.py --help
python -m py_compile \
  /Users/champion/Documents/develop/skills/xhs-content-pipeline/run_skill.py \
  /Users/champion/Documents/develop/skills/xhs-content-pipeline/whisper_transcribe.py
```

真实本机闭环结果：

- wiki：`/Users/champion/Documents/develop/Wiki/ClaudeWiki/lufei-xhs-wiki`
- 入库笔记：`69da513a0000000023005dfa`
- manifest 文件数：12
- query 命中数：2
- Obsidian 打开方式：`obsidian-cli:open`

## 下一步运营使用方式

1. 在 Hermes WebUI 输入一条小红书笔记链接，调用 `xhs_extract_note`。
2. 调 `xhs_ingest_note_to_wiki` 写入 Obsidian wiki。
3. 调 `xhs_query_wiki_context` 读取“压力面 / 作品集 / Web Coding”等上下文。
4. 调 `xhs_run_content_skill(skill="viral-analysis")` 做十维拆解。
5. 调 `xhs_run_content_skill(skill="topic-selection")` 生成 50 候选并筛 Top 5。
6. 调 `xhs_run_content_skill(skill="script-generation")` 生成互动标注版逐字稿。
7. 路飞真人录制，发布后再把真实互动数据和评论回灌 wiki。
