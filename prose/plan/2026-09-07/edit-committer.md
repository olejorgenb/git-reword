# Plan: edit committer and committer date

Follow-up to `2026-09-06/edit-author-date.md` (landed as 87d5cc8), which
made `Commit:` and `CommitDate:` display only and warned when they were
edited. That was presented as git's behaviour; it is not. `git
commit-tree` reads `GIT_COMMITTER_NAME`, `GIT_COMMITTER_EMAIL` and
`GIT_COMMITTER_DATE` exactly like the author variables; "current user,
now" is only the default when they are unset. So the committer pair gets
the same treatment as the author pair.

## Decisions

- **All four keys are editable.** `Commit` and `CommitDate` are read
  back, validated and normalised through `git var GIT_COMMITTER_IDENT`
  (same shape as `GIT_AUTHOR_IDENT`, verified on git 2.55), and applied
  through the `GIT_COMMITTER_*` variables in `commit_tree`. The
  `committer-edited` warning goes away.
- **What is in the file is what the commit gets.** When a block carries a
  `Commit:` or `CommitDate:` line, its value, edited or not, is the
  committer of the re-minted commit. A block without the line, and every
  commit re-minted without a block (descendants after the range), gets
  git's default: the current user, now. So `--commit-info` alone keeps
  the committer and committer date of every re-minted commit in the
  range, which is what `git filter-repo` does; without the flag the tool
  keeps behaving like `rebase` and `commit --amend`. This is the simplest
  reading of the file and the one that gives control.

  The alternative is to apply only *edited* values and reset the rest,
  like the author pair where "unchanged" and "default" happen to
  coincide. That keeps the rewrite outcome independent of the write
  flags, but then an unchanged `CommitDate:` line shows a date the commit
  will not have, which is the confusion this plan is removing.
- **"Changed" stays about the range as written.** A commit is changed
  when its message, author, author date, committer or committer date in
  the file differs from the original. An unchanged block with committer
  lines is not a change; it only matters if the block is re-minted for
  another reason, and then the lines say what it keeps.
- **Change listing** prints `Commit: old -> new` and `CommitDate: old ->
  new` like the author lines.

## Steps

1. **Spec.** "Line classification": all four keys are editable; a
   committer line present in a block is applied to a re-minted commit,
   absent lines mean git's default. "Reading back": the definition of
   "changed" gains the committer pair; validation covers
   `GIT_COMMITTER_IDENT`. The `--commit-info` flag text loses "display
   only" and gains the keep-the-committer note. Commit before the code.
2. **git.py.** `author_ident` becomes `ident(kind, value, date, cwd)`
   with `kind` in `author`/`committer`, or a second function
   `committer_ident`; one implementation, the env prefix and the `git var`
   name differ. `commit_tree` gains `committer: str | None` and
   `committer_date: str | None`, setting the three `GIT_COMMITTER_*`
   variables when given.
3. **apply.py.** `Edit` gains `committer` and `committer_date`. In
   `block_edit`, a committer line's value goes through `committer_ident`
   when it differs textually from the original, and the result is kept
   on the `Edit` whether or not it equals the original (the file says
   what the commit gets); an unchanged line is kept without a git call.
   `differs_from` compares the normalised committer values against the
   commit's. `apply` passes the `Edit`'s committer to `commit_tree`. The
   `committer-edited` diagnostic is removed; `bad-committer` and
   `bad-committer-date` mirror the author ones.
4. **cli.py.** The listing prints the two extra lines when they differ
   from the original. `--commit-info` help: "Add Commit and CommitDate
   info lines per commit (editable; keeps the committer of rewritten
   commits)".
5. **Language server.** `changed_parts` gains "committer"; the hover is
   unchanged; the revert action restores committer lines like author
   lines. The `committer-edited` code goes away.
6. **Tests.** CLI: `--commit-info` with no edits and a message change
   keeps `%cn %ce %cd` on the changed commit and resets them on a
   carried-along descendant; a committer search-and-replace changes `%ce`;
   a bad `CommitDate:` is an error with a line number; the
   author-plus-committer replace no longer warns. LSP: the diagnostics
   and revert. Format: nothing changes.
7. **Docs.** README flag line.

## Open

- Whether an `Author:` line present but unchanged should also mean
  "keep", which it already does since original and default coincide.
  Nothing to do.
