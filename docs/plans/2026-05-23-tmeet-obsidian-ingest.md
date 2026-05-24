# Tencent Meeting to Obsidian Ingest Plan

Date: 2026-05-23

## Goal

Export LuFei Tencent Meeting records through the official `tmeet` CLI and
persist them into the local Obsidian llm-wiki raw layer.

This phase deliberately does not build CRM, paid products, or a mock-interview
bot. It only turns meeting recordings, transcripts, and smart minutes into
local files that Hermes can later query.

## Implemented Scope

1. Detect and call `tmeet`.
2. Fall back to `go run` from `/Users/champion/Documents/develop/tencentmeeting-cli`
   when no global `tmeet` binary exists.
3. Prefer the local compiled binary at
   `/Users/champion/Documents/develop/tencentmeeting-cli/tmeet` when present.
4. Export:
   - ended meeting list
   - record list
   - record address metadata
   - transcript paragraphs
   - smart minutes
5. Classify by meeting title:
   - `coaching`
   - `courses`
   - `unknown`
6. Write Obsidian files under:

   ```text
   /Users/champion/Documents/develop/Wiki/ClaudeWiki/lufei-xhs-wiki/
     raw/tencent-meetings/
       coaching/
       courses/
       unknown/
       index.md
       manifest.json
     _derived/tencent-meetings-manifest.json
   ```

## Entry Points

CLI:

```bash
python scripts/tmeet_export_records_to_obsidian.py \
  --start 2026-05-01 \
  --end 2026-05-23
```

Hermes tool:

```text
tmeet_export_records_to_obsidian
```

The `tencentmeeting` plugin is enabled in `~/.hermes/config.yaml`.

Required inputs:

- `start`
- `end`

Optional inputs:

- `wiki_path`
- `tmeet_bin`
- `tmeet_repo`
- `dry_run`

## Self-Test

The automated test uses fixture JSON files and does not require Tencent Meeting
login:

```bash
python -m pytest -o addopts='' tests/scripts/test_tmeet_export_records_to_obsidian.py -q
```

Current result:

```text
2 passed
```

## Real Export Prerequisite

The local `tmeet` source fallback is runnable. Current auth state:

```text
Not logged in. Please use 'tmeet auth login' to authenticate.
```

Real export will work after Tencent Meeting OAuth login is completed on the
machine that runs the script.

Local binary built:

```text
/Users/champion/Documents/develop/tencentmeeting-cli/tmeet
```
