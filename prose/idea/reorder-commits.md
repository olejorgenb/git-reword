# Reordering commits in the edit file

Jotted 2026-09-03.

It would be awesome to reorder commits by moving blocks around in the edit
file. The apply step is already a rebase plan: `apply.py` rewrites the todo
that `git rebase -i` produces, so emitting `pick` lines in file order
instead of todo order is a small change.

## Things to decide

- **Order is meaningful.** Today the order of blocks is free. With
  reordering, the file order becomes the new history order, oldest first,
  like the rebase todo. The writer already emits oldest first
  (`rev-list --reverse`), but the spec should state that order matters.
- **Missing blocks.** Silently dropping a commit whose block was deleted is
  too dangerous for a reword tool. Options: error (safest, current
  behaviour is implicitly "unchanged"), or an explicit `drop <sha>` line
  mirroring the rebase todo. Error first, `drop` later if wanted.
- **Conflicts.** Reordering can conflict where rewording never does. The
  rebase stops with the usual git state; `--continue` already exists in
  the CLI, so the flow is the same as with any rebase. Diagnostics could
  warn "moving <sha> before <sha>" but cannot predict conflicts without
  trying.
- **Other todo verbs.** Once order is editable, `fixup`/`squash` are the
  obvious next ask. That turns the format into a rebase todo with full
  messages, which may be exactly the point, or scope creep. Decide when it
  comes up.
- **Editor support.** A code action "Move commit up/down" is easy on the
  LSP side (swap two block ranges in one `WorkspaceEdit`). The outline
  already shows the order.

Related: the commit-tree chain idea (build the new commits directly and
`update-ref`, no rebase) handles reordering less naturally, since it
assumes trees stay the same. Reordering needs the real rebase.
