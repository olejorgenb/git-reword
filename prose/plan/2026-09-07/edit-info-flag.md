# Plan: gate info editing behind `--edit-info`

Follow-up to `2026-09-06/edit-author-date.md` (landed as 87d5cc8). That
made `Author`/`AuthorDate` editable and `Commit`/`CommitDate` display
only, and called the latter git's behaviour. It is not: `git commit-tree`
reads `GIT_COMMITTER_NAME`, `GIT_COMMITTER_EMAIL` and `GIT_COMMITTER_DATE`
like the author variables; "current user, now" is only the default.

Editing commit metadata at all is the fringe case. Rewording is the
default, and there the info lines are context. So editing is opted into
with one flag, and with it every info line in the file is applied as
written.

## Decisions

- **`--author-info` and `--commit-info` stay display flags.** They write
  the same lines as today. Without `--edit-info` an edited info
  line is a warning ("info lines are display only; rerun with
  `--edit-info` to apply edits"), for all four keys, and the run
  proceeds with the original metadata. Unknown keys stay errors.
- **`--edit-info` makes info lines authoritative.** With it, every
  info line present in a block is applied to that commit when it is
  re-minted, edited or not: `Author`/`AuthorDate` through `GIT_AUTHOR_*`,
  `Commit`/`CommitDate` through `GIT_COMMITTER_*`. A key absent from a
  block, and every commit re-minted without a block (descendants after
  the range), gets git's default: original author, current user and time
  as committer. So `--edit-info --author-info` edits authors and
  gives rewritten commits new committer dates, as today; adding
  `--commit-info` keeps or edits the committer too. The flag implies
  `--author-info`, since alone it has nothing to edit.
- **The file carries the mode.** The language server is started by the
  editor and `--continue` reopens an old file, so the flag cannot travel
  on the command line. The writer emits a directive line in the header:

  ```
  # git-reword-options: edit-info
  ```

  `parse` reads `# git-reword-options:` lines anywhere before the first
  `commit` line into `ParseResult.options: set[str]`; an unknown option
  is an error. The header text adapts: with the flag it says the info
  lines are applied as written, without it that they are context.
- **Committer values are validated like author values**, through
  `git var GIT_COMMITTER_IDENT` (same output shape, verified on git 2.55
  on 2026-09-07), only for lines that differ textually from the original.
- **"Changed"**: a commit is changed when its message differs, or, with
  the option, when a normalised info value differs from the original. An
  unchanged committer line is not a change by itself; it says what the
  commit keeps if it is re-minted for another reason. The change listing
  prints `Commit: old -> new` and `CommitDate: old -> new` like the
  author lines.

## Steps

1. **Spec.** Writing: `--edit-info` and the directive line under
   Flags; `--commit-info` loses "display only". Line classification: info
   lines are context unless the file carries `edit-info`, then
   every present line is applied; editing one without it is a warning.
   Reading back: the directive, the two `git var` checks, the definition
   of "changed", and what an absent line means on a re-minted commit.
   Commit before the code.
2. **git.py.** `author_ident` becomes `ident(role, value, date, cwd)` with
   `role` in `("author", "committer")` choosing the env prefix and the
   `git var` name; `commit_tree` gains `committer` and `committer_date`
   (None means unset, so git's default).
3. **format.py.** `OPTIONS_RE` for the directive, `ParseResult.options`,
   `KNOWN_OPTIONS = {"edit-info"}`, `write(..., edit_info: bool)`
   emitting the directive and the adapted header. `INFO_KEYS` stays;
   `AUTHOR_KEYS`/`COMMITTER_KEYS` split is still used by the writer.
4. **apply.py.** `Edit` gains `committer` and `committer_date`.
   `block_edit(block, commit, *, edit_info, cwd)`: without `edit_info`
   every textually changed info line is an `info-display-only` warning
   and the `Edit` carries only the message; with it, each present key is
   normalised (via `ident` when changed, kept as the original value when
   not) and stored on the `Edit`, errors `bad-author`, `bad-author-date`,
   `bad-committer`, `bad-committer-date`. `committer-edited` goes away.
   `apply` passes committer and committer date to `commit_tree`.
5. **cli.py.** The flag, implying `--author-info`; `resolve` passes
   `result.options`; the listing prints the two extra lines.
6. **Language server.** `Analysis` reads `result.options`; `changed_parts`
   gains "committer"; the revert action restores committer lines like
   author lines. Nothing else changes.
7. **Tests.** Format: the directive round-trips, an unknown option is an
   error, the header text differs. CLI: without the flag an author edit
   warns and applies nothing; with the flag and `--author-info` the
   search-and-replace test passes as today and `%cd` moves; with
   `--commit-info` too, a message-only change keeps `%cn %ce %cd` on the
   changed commit and resets them on a carried-along descendant; a
   committer replace changes `%ce`; a bad `CommitDate:` is an error with
   a line number; `--continue` picks the mode up from the file. LSP: the
   display-only warning without the directive, the four errors and the
   revert with it.
8. **Docs.** README: the flag, one line on the directive.

## Open

- Nothing. The name was `--edit-commit-info` first; `--edit-info` was
  chosen (2026-09-07) because next to `--commit-info`, the committer
  lines, the longer name read as "edit the committer lines".
