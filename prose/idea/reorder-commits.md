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

## Rethink 2026-09-04: no rebase at all

Editor side is already solved: with the LSP folding ranges, fold-all
collapses each commit to its `commit` line, and Zed moves a folded block
as one unit (move line up/down unfolds, moves, refolds; cut and paste on
the folded line take the whole block). The message travels with the sha.

Apply side: the rebase design has a conflict problem. Rewording never
conflicts, so `apply.py` assumes the rebase runs to completion inside the
tool; the scripted editors live in a temp dir. Reordering can conflict,
and after `git rebase --continue` from the shell the remaining `reword`
lines open the user's real editor with the old messages.

Better: check for conflicts up front with `git merge-tree --write-tree`
(git 2.38+), which replays a commit onto a new parent in memory and
exits non-zero with the conflicting paths. For each commit in file order,
merge-base = original parent, sides = chain so far and the commit. Once
the trees exist, the rebase is redundant: `commit-tree` each with the new
message and original author, `update-ref` the branch once at the end.

- Atomic: the whole chain or nothing. No stopped rebase, no abort text.
- Simpler than today: the sequence and message editor scripts go away.
  Reword-only is the case where every merge is trivial.
- A dirty worktree is fine when the final tree equals the old HEAD tree
  (always for reword-only); otherwise require a clean one, like rebase.
- No hooks run. Signing works through `commit-tree` and `commit.gpgsign`.
- Merges: refuse reordering, keep the current behaviour. Not a goal.

Not started; parked as costing more than it tastes on 2026-09-04, but the
merge-tree route makes it a loop of three plumbing commands plus a spec
line saying order matters.

Update 2026-09-06: the apply side is done
(`prose/plan/2026-09-06/commit-tree-apply.md`). `apply.py` walks HEAD's
history parents-first with an old-to-new sha map, so a moved parent is
already handled. What remains for reordering is the `merge-tree
--write-tree` step for the tree of a moved commit, a dirty-worktree check
(the tree can change now), and the spec saying order matters.
