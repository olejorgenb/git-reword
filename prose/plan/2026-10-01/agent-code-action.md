# Plan: ask Zed's agent to help with messages

Two code actions open Zed's agent panel with a prompt already written. You
then work on the messages together with the agent, and it edits the edit
file itself. Only Zed's built-in agent panel is covered. An external
terminal and the exchange directory are left for later, if ever.

## Why this shape

- `zed://agent?prompt=<text>` opens the agent panel with a new thread and
  the prompt filled in. It is upstream since v0.223, with safeguards since
  v0.228 (see `~/contrib/zed`, `crates/zed/src/zed/open_listener.rs:223`).
  Zed never sends it on its own: you press enter, and a banner says the
  prompt came from outside. Zed removes control characters and turns runs
  of three or more newlines into two. The thread uses the agent the panel
  is set to.
- The agent edits through Zed's buffers, so its edits land in the open
  `REWORD_EDITMSG` even when it has unsaved changes. Zed shows them as a
  diff to accept or reject. Nothing has to come back through the language
  server.
- The edit file is at the worktree root (`reword-format.md`, File
  location), so it is within the agent's reach. A context file in a temp
  directory would not be, because the agent's file tools only see the
  project. So there is no context file. The prompt holds the instructions,
  and the agent runs `git show` itself when it needs a diff.
- Zed's built-in agent has a diagnostics tool
  (`crates/agent/src/tools/diagnostics_tool.rs`), so it can see this
  server's diagnostics and fix them. Other agents in the panel (Claude
  Code over ACP) may not. The prompt mentions diagnostics without
  depending on them.

## Actions

| Title | Kind | When |
|---|---|---|
| Discuss `<sha>` with agent | (command) | client is Zed and the block is a known commit |
| Discuss all messages with agent | (command) | client is Zed and the file has two or more known commits |

Both are commands that run the existing `git-reword.openCommit` command
with the `zed://agent` URL. That command already opens any URL: it uses
`window/showDocument` when the client has it, otherwise `zed <url>`.
Renaming it to `git-reword.openUrl` is a separate cleanup; it is not done
here. Offering the actions costs nothing: the prompt is built from the
text and the cached commit lookup, with no other git calls.

## Prompt

Plain text, built in `analysis.py`. It contains:

1. **The task.** For one commit: "Help me improve the commit message of
   `<full sha>` (block at line N) in `<path>`." For all of them: "Help me
   improve the commit messages in `<path>`, as a series." Then: discuss
   first, edit once we agree, and keep edits to message lines (and info
   lines in `edit-info` mode).
2. **The repo.** Worktree root, and the commits as `<short sha>
   <subject>` lines (only the target for the per-commit action). It
   suggests `git show --stat --patch <sha>` for the diff and `git log` for
   the house style, without including either.
3. **The syntax guide** (see below).
4. **The limits.** Messages (and `edit-info` info lines) can change.
   Commits cannot be split, squashed, reordered, added or dropped, and
   `commit` lines must stay as they are. Do not run `git reword` or commit
   anything; the user finishes the reword.
5. **Diagnostics.** If you can see diagnostics for the file, fix the ones
   in the lines you touched.

Size: a few hundred words plus one line per commit. A URL of that size is
fine for both `showDocument` and argv. Cap the commit list at about 100
lines; past that, write "and N more", since the agent can read the file.

## Syntax guide

A new constant `AGENT_GUIDE` in `format.py`, next to `HEADER`, so it
changes along with the format. It is short and hand-written:

- The structure is in column 0: `commit <sha>` lines, `Key: value` info
  lines, and `#` comments. Comments are dropped when the file is applied.
- A message is the block of lines indented 4 spaces under its commit and
  info lines. Its first line is the subject, then a blank line, then the
  body. In the file, a blank line inside a message is just empty.
- Trailers (`Key: value`) go in the last paragraph of the message.
- `#` comment blocks after a message (the file stats, links) are
  information only and are not part of the message.
- With the `git-reword-options: edit-info` directive in the header, the
  author info lines are applied as written. Without it, info lines are
  display only.

Check it against `reword-format.md` while writing it. Wherever the two
differ, the spec wins.

## Steps (one commit each)

1. **Spec.** `prose/spec/lsp-code-actions.md`: the two table rows and a
   section "Discussing messages with Zed's agent" covering the points
   above (URL, never sent on its own, edits through the buffer, no
   context file, limits). `reword-format.md`: list the actions under
   Consumers.
2. **`AGENT_GUIDE`** in `format.py`, with a test that it names the
   current header directive (`EDIT_INFO_OPTION`), so the two stay in step.
3. **Prompt and actions.**
   - `Analysis.agent_prompt(blocks, *, focus)` builds the prompt text.
   - `agent_url(prompt)` returns `"zed://agent?" + urlencode({"prompt": prompt})`.
   - `_agent_actions(block)` is added to `code_actions()` after
     `_open_actions`.
   - Tests:
     - not offered when the client isn't Zed;
     - per-block and file-wide titles;
     - the URL round-trips through `parse_qs` to the prompt;
     - the prompt names the full sha, the path and the limits;
     - in `edit-info` mode it says info lines may change;
     - the existing title-list tests are filtered as with `not_stats()`.
4. **Manual check in Zed**:
   - the action opens the panel with the prompt;
   - the agent can read and edit `REWORD_EDITMSG` while it has unsaved
     changes;
   - whether Claude Code over ACP also edits the buffer and not the file
     on disk;
   - whether the built-in agent sees our diagnostics.

## Open points

- **Path in the prompt.** Use the absolute path or the path relative to
  the worktree root? Zed's tools take project paths. If the edit file was
  opened as a single-file workspace, the agent may not see it. Start with
  the relative path plus the root, and adjust after the manual check.
- **Which window.** `zed://agent` goes to the active Zed window. That's
  fine when the action is run from the edit file, which is the normal case.
- **Agent choice.** The URL can't choose the agent; the panel's current
  one is used. Not something this server can change.
