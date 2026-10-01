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
- **Git cost.** Zed asks for code actions on every cursor move, so stats
  are cached per full sha on `Repo`, next to `_commits`. The per-block
  action costs one batched lookup per new block. The all-commits action
  does one batched lookup for the uncached shas the first time it's
  offered, and nothing after that. `codeAction/resolve` would make the
  edit lazy, but caching is simpler and enough.
- **Batched lookup.** `get_stats` takes a range today. It gains a form
  that takes explicit shas and runs `git log --no-walk=unsorted`, so
  that one call (two, as today: `--name-status` and `--shortstat`)
  covers any set of commits.
- **No "remove" action.** Deleting a folded comment run is easy by
  hand. Add one later if it turns out to be wanted.

## Steps

1. **Spec.** `lsp-code-actions.md`: two rows in the Actions table, and a
   short "Adding file stats" section with the placement, the "already
   has one" rule, merges and caching. Commit before the code.
2. **git.py.** `_stat_records` takes a list of revisions plus a `walk`
   flag (`--no-walk=unsorted` when false, before `--end-of-options`).
   `get_stats(commit_range)` stays as it is; add
   `get_commit_stats(shas, cwd)` built on the same helper.
3. **format.py.** `stat_lines(stat) -> str`, the block text including
   its leading blank line, used by `write`.
4. **analysis.py.**
   - `Repo.stats(shas) -> dict[str, Stat | None]`, cached, one
     `get_commit_stats` call for the misses.
   - `Analysis.has_stat(block)` using a `_STAT_SUMMARY_RE`.
   - `Analysis.stat_edit(block, stat) -> lsp.TextEdit`, an insert at
     the message end.
   - The two actions in `code_actions`, after Reflow and before the
     open actions. They need a repo and a resolved commit.
5. **Tests** (`test_lsp.py`, `test_git.py`):
   - Inserting into a file written without `--stat` gives the same text
     as `fmt.write(..., stats=...)`, both for the per-block action and
     for all commits.
   - No action on a block that already has a stat block, or on a merge.
   - The all-commits action is hidden when fewer than two blocks lack
     stats.
   - `get_commit_stats` matches `get_stats` for the same commits.
