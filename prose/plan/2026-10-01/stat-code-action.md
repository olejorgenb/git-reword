# Plan: code action to add `--stat` blocks after the fact

`--stat` is opt-in, chosen when the file is written. Once you're in the
editor and want the file list for a commit, you have to abort and rerun.
A code action can insert the same block the writer would have written.

## Decisions

- **Two actions, both `refactor.rewrite`.**
  - `Add file stats to <sha>`: for the block under the cursor, when it
    has no stat block and is not a merge.
  - `Add file stats to all commits`: from any line, when at least two
    non-merge blocks lack a stat block (with one, it would just duplicate
    the per-block action). Covers every such block in the file.
- **Same text as the writer.** The block is rendered by one function
  shared with `fmt.write` (extracted from the inline loop at
  `format.py:341`), so the two can't drift: one blank line, the summary
  line, one line per file, all with `#` and three spaces.
- **Where it goes.** At the end of the message (`message_lines` end),
  before any trailing comments or blanks the block already has. That's
  where the writer puts it, and the blank line between blocks stays.
- **"Already has one"** means that the comment lines after the message
  include a summary-shaped line: `#   <n> file(s) changed...` or
  `#   no files changed`. As with stat links, the server recognises the
  shape and not where it came from. A hand-written block of that shape
  counts.
- **Merges** get no action, as with the flag: `get_stats` already
  returns None for them.
- **The edit is computed lazily, via `codeAction/resolve`.** Editors ask
  for code actions on cursor moves, so offering the action must not run
  git. `textDocument/codeAction` returns the two actions with a title,
  kind and `data` but no `edit`. Deciding to offer them is a text check
  (the "already has one" rule below). `codeAction/resolve` runs git and
  fills in `edit`. Zed advertises resolve support for `edit`
  (`crates/lsp/src/lsp.rs` in the fork).
  - `data` is `{"uri": ..., "shas": [<full sha>, ...]}`, the blocks to
    cover. At resolve time the document is analysed again from its
    current text, and the edit goes to the blocks that still exist and
    still lack a stat block. So an edit made between the menu opening
    and the pick does not misplace the insert.
  - Clients that don't advertise resolve support for `edit` get the edit
    computed eagerly in the `codeAction` response, by the same function.
    That costs a git call per request, but only in those editors, and it
    keeps the action working everywhere.
  - No cache: git runs once per pick.
- **Batched lookup.** `get_stats` takes a range today. It gains a form
  that takes explicit shas and runs `git log --no-walk=unsorted`, so
  that one call (two, as today: `--name-status` and `--shortstat`)
  covers any set of commits.
- **No "remove" action.** Deleting a folded comment run is easy by
  hand. Add one later if it turns out to be wanted.

## Steps

1. **Spec.** `lsp-code-actions.md`: two rows in the Actions table, and a
   short "Adding file stats" section with the placement, the "already
   has one" rule, merges and the lazy edit (resolve, eager fallback).
   Commit before the code.
2. **git.py.** `_stat_records` takes a list of revisions plus a `walk`
   flag (`--no-walk=unsorted` when false, before `--end-of-options`).
   `get_stats(commit_range)` stays as it is; add
   `get_commit_stats(shas, cwd)` built on the same helper.
3. **format.py.** `stat_lines(stat) -> str`, the block text including
   its leading blank line, used by `write`.
4. **analysis.py.**
   - `Analysis.has_stat(block)` using a `_STAT_SUMMARY_RE`.
   - `Analysis.stat_edits(shas) -> list[lsp.TextEdit]`: one
     `get_commit_stats` call, then an insert at the message end of each
     matching block that still lacks a stat block and isn't a merge.
   - The two actions in `code_actions`, after Reflow and before the open
     actions, with `data` and no edit. They need a repo. `code_actions`
     takes a `resolve: bool` (from the server). When it's false, the
     edit is filled in at once with `stat_edits`.
   - Merges are skipped when offering, using `Commit.parents` from the
     `Repo.commit` lookup. That lookup is already cached and already
     done for diagnostics, so it adds no git calls.
5. **server.py.** `CodeActionOptions(resolve_provider=True)`. Read the
   client's `textDocument.codeAction.resolveSupport.properties` at
   initialize and pass "edit is resolvable" into `code_actions`. A
   `CODE_ACTION_RESOLVE` handler re-analyses `data["uri"]` and sets
   `action.edit` from `stat_edits(data["shas"])`.
6. **Tests** (`test_lsp.py`, `test_git.py`):
   - Offering the actions runs no stat lookup (the action has no edit
     when resolve is supported).
   - Resolving after an unrelated edit above the block still inserts at
     the right line.
   - Inserting into a file written without `--stat` gives the same text
     as `fmt.write(..., stats=...)`, both for the per-block action and
     for all commits.
   - No action on a block that already has a stat block, or on a merge.
   - The all-commits action is hidden when fewer than two blocks lack
     stats.
   - `get_commit_stats` matches `get_stats` for the same commits.
