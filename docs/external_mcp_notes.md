# External MCP Server Notes

This file tracks observed quirks of *other* MCP servers we interact with from this project (typically during development/testing). It is **not** about the local `gitlab_mcp` we are building.

## Hosted external GitLab MCP (via claude.ai)

### Diff endpoints do not return file content

Observed against a merge request on a self-hosted GitLab instance.

| Tool | Result |
|---|---|
| `get_merge_request_changes` | Errors `operation failed` for both `project_id` as string path and as int ID. |
| `get_merge_request_commits` | Errors `operation failed`. |
| `compare_branches` | Returns `{"commits": [], "diffs": []}` even when `changes_count > 0`. Tried branch names, SHAs (`from_ref`/`to_ref`), and `straight=true` — all empty. |
| `get_commit` | Returns commit metadata only — no `diffs`, `stats`, or files. |

What does still work:

- `get_merge_request` — full MR metadata including `diff_refs` (`base_sha`/`head_sha`) and `changes_count`.
- `list_commits` with `ref=<branch>` — commit list with SHAs, but no per-commit diffs.

### Workarounds when you need actual diff content

1. **Web UI** — append `/diffs` to the MR's `web_url`, e.g. `…/merge_requests/<iid>/diffs`.
2. **Local clone** — `git clone https://…/<project>.git && git diff <base_sha>...<head_sha>`. Requires a GitLab PAT.

The external MCP source is not in this repo, so the fix lives upstream. Don't waste cycles permuting parameters inside the broken tools — fall back to one of the workarounds.
