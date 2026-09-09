# CLI Guide

Meeting Agent provides an interactive terminal frontend for local operations, quick diagnosis, and environments without a browser.

Source: `backend/scripts/cli_agent.py`.

## 1. Start the CLI

```bash
cd backend
uv run python -m scripts.cli_agent
```

Enter `/help` after startup to list commands.

## 2. Command reference

### 2.1 Meetings and files

- `/meetings [--limit n] [--offset n]`: paginated meeting list.
- `/meeting <meeting_id>`: meeting details.
- `/files <meeting_id> [--limit n] [--offset n]`: paginated file list.
- `/upload <path> [--meeting id] [--title t] [--description d] [--wait]`: upload a local file.
- `/reprocess <meeting_id> [--wait]`: reprocess every file in a meeting.
- `/transcript <meeting_id> [--file file_id]`: show a transcript.
- `/summary <meeting_id>`: generate a meeting summary.
- `/export <meeting_id> --format markdown|json|txt [--output path]`: export a meeting.

### 2.2 Retrieval and chat

- `/search [query]`: search through MCP tools.
- `/retrieve <query> [--meeting 1,2] [--top-k n]`: retrieve without LLM generation.
- `/chat_stream <question> [--meeting 1,2] [--top-k n] [--web]`: stream an answer.
- Plain text input: run standard `ask()` chat.

### 2.3 Sessions

- `/sessions [--limit n] [--offset n]`: paginated session list.
- `/session <session_id>`: show session messages.
- `/session_use <session_id>`: bind the current chat to a session.
- `/session_delete <session_id>`: delete a session.

### 2.4 Settings

- `/settings get [dotted.path]`: read all settings or one setting.
- `/settings set <dotted.path> <value>`: update a setting.
- `/settings keys [prefix]`: list configurable keys.
- `/settings bindings`: show available provider bindings.
- `/settings reload`: reload `config/main.yaml`.
- `/settings rebuild`: trigger a background vector rebuild.
- `/settings wizard <section>`: interactive setup for `llm|embedding|rag|memory|search|upload`.

Compatibility aliases: `/settings_get` and `/settings_set`.

### 2.5 Diagnostics and helpers

- `/status`: diagnose DB, vector store, LLM, embedding, and MCP tools.
- `/skills`, `/skill_match`, `/skill_invoke`: debug the Skill system.
- `/memory`: interactive memory CRUD mode.
- `/clear`: clear current CLI conversation context.
- `/quit`: exit.

## 3. Typical workflows

### 3.1 Upload and wait

```text
/upload "~/Downloads/spec.pdf" --title "Spec Review" --wait
/meeting 12
/files 12
```

### 3.2 Retrieve and stream an answer

```text
/retrieve "action items" --meeting 12 --top-k 8
/chat_stream "Summarize the risks from this review" --meeting 12 --top-k 6
```

### 3.3 Export locally

```text
/export 12 --format json --output ./exports/meeting-12.json
```

## 4. Interactive prompts

Most commands prompt for missing arguments instead of failing immediately. For example, `/meeting` asks for a meeting ID, `/settings set` asks for a key and value, and `/upload` asks for a path and whether to wait.

## 5. Important boundaries

- The CLI and HTTP API share the same database and vector store; CLI upload, deletion, and reprocessing affect web-visible data.
- `/settings set` updates runtime memory; a restart restores file/environment defaults.
- Exports default to `exports/` in the current working directory; `--output` overrides the destination.

## 6. Test coverage

CLI automation is in:

- `backend/tests/tools/test_cli_agent_parser.py` for command parsing;
- `backend/tests/tools/test_cli_agent_e2e.py` for subprocess E2E, interaction, and export.
