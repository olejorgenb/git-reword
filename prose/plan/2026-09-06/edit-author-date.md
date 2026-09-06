# Plan: edit author and author date

Depends on `commit-tree-apply.md` (landed as 4d04ab5): with `commit-tree`
the author is three env vars per commit, so editing it is a matter of
reading the info lines back. Use case: a run of commits made with the wrong `user.email`, fixed
by search and replace in the edit file.

Spec: `prose/spec/reword-format.md`, "Line classification", "Writing",
"Reading back". The spec changes are step 1 and should be committed first.

## Decisions

- **Keys follow `git log --pretty=fuller`**: `Author:`, `AuthorDate:`,
  `Commit:`, `CommitDate:`. The current `Date:` (author date) is renamed;
  nothing reads it yet so the rename is free now. `--info` emits all four
  so the two identities are visible side by side, which is the point when
  the committer is about to be reset by the rewrite anyway.
- **`Author` and `AuthorDate` are editable.** `Commit` and `CommitDate`
  are display only: every re-minted commit gets the current user and now,
  as with any rewrite in git. An edited `Commit`/`CommitDate` line is a
  warning, not an error, because a search and replace on the author name
  will hit the `Commit:` line too and should not block the run. The CLI
  prints the warning per block; the language server shows it as a
  warning diagnostic.
- **Unknown keys are errors.** Today info lines are ignored on parse; once
  `Author:` does something, `Autor:` must not be silently dropped.
- **Values are validated by git**, not by regex: `git var GIT_AUTHOR_IDENT`
  with `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`, `GIT_AUTHOR_DATE` set fails
  on a malformed date or an empty name and otherwise prints the canonical
  ident with a raw timestamp. That gives validation and normalisation in
  one call, run only for blocks whose lines differ textually from what
  was written. The name/email split is on the last ` <`; a value without
  `<...>` is an error before git is asked.
- **A commit is changed** when its message, author ident or author date
  differs from the original after normalisation. Same date written with
  a different offset is a change (git stores the offset).

## Steps

1. **Spec.** Rename `Date:` to `AuthorDate:`, add `Commit:` and
   `CommitDate:` to the example and to `--info`. "Line classification":
   info lines with a known key are read back; `Author` and `AuthorDate`
   are applied, `Commit` and `CommitDate` are display only and editing
   them is a warning, unknown keys are errors. "Reading back": the
   definition of "changed" gains author and author date, and a sentence on
   validation through `git var`. Commit before the code.
2. **git.py.** `Commit` gains `committer: str` and `committer_date: str`
   (`%cn <%ce>`, `%cd` appended to `_LOG_FORMAT`; `history` gets them for
   free); `date` is renamed `author_date`. The rename touches the hover in
   `lsp/analysis.py` and one `Commit(...)` in `tests/test_format.py`.
   `commit_tree` already takes `author` and `author_date`. `_split_ident`
   exists and raises `GitError` on a value without `<email>`; make it
   public so the CLI and the language server can check a line before git
   is asked. New `author_ident(author, date, cwd) -> (name, email, raw
   date)` wrapping `git var GIT_AUTHOR_IDENT` with the three env vars set;
   raises `GitError` with git's message ("invalid date format: ...",
   "empty ident name ...") on bad input. Both verified against git 2.55
   on 2026-09-06.
3. **format.py.** `write` emits the four lines under `--info`, aligned
   like `git log --pretty=fuller`. `parse` keeps `Block.info` as the raw
   `{key: value}`; it does not know which keys mean what, so the grammar
   and the parser stay format-only. A new `INFO_KEYS` constant lists the
   four with their editability for the two consumers.
4. **cli.py and apply.py.** `resolve` returns `{sha: Edit}` where
   `Edit(message, author, author_date)`; `author`/`author_date` are
   `None` when the line is absent or textually unchanged. Errors for an
   unknown key and a value without `<email>`; warnings for edited
   `Commit`/`CommitDate`. Values that changed go through `author_ident`,
   errors reported as `path:line: error: <git's message>`.
   `changed_commits(commits, edits)` compares all three and returns
   `(commit, Edit)` pairs; `apply(plan, changes)` keeps its shape and, in
   the mint loop, takes message, author and author date from the `Edit`
   when the commit has one, from the `Commit` otherwise. The change
   listing prints `Author: old -> new` and `AuthorDate: old -> new`
   lines above the message diff when they differ.
5. **Language server.** Hover on a sha shows all four. Diagnostics:
   `unknown-info-key` (error), `bad-author` (error, no `<email>`),
   `committer-edited` (warning). `bad-date` needs git; run `author_ident`
   in the analysis pass for edited lines only, like other git-backed
   checks. The "revert to original" code action restores the info lines
   too when they were emitted.
6. **Grammar and Zed.** No change: `info_line` already covers the lines.
   If the key should be highlighted differently for editable versus
   display-only keys, that is a `highlights.scm` query on the key text;
   skip unless it turns out useful.
7. **Tests.** Format: write with `--info` has all four lines and parses
   back into `info`. CLI: author search-and-replace across three commits
   changes `%an %ae` on exactly those and nothing else; date edit
   round-trips through `--date=raw`; bad date and missing `<email>` are
   reported with line numbers and nothing is written; edited `Commit:`
   warns and the run proceeds; `Autor:` is an error; unchanged `--info`
   lines are not a change. LSP: the three diagnostics and the hover.
8. **Docs.** README flag description and the example; mention the
   search-and-replace use case in one line.

## Open

- Whether `--info` should default on, or get a config key, once the lines
  are useful for editing. Off for now; the flag is one word.
- Whether an unchanged `Author:` line on a re-minted commit should ever
  mean "keep the committer too" (`--committer-date-is-author-date` style).
  No: git's own rewrites reset the committer, and a flag can do it later.
