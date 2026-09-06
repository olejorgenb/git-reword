# Plan: apply with `commit-tree` instead of `rebase -i`

Origin: `prose/idea/reorder-commits.md`, "Rethink 2026-09-04". Motivated
on 2026-09-06 by wanting to edit author and date, which the rebase apply
has no channel for (see `edit-author-date.md`, which builds on this).

## Why

The rebase apply drives `git rebase -i` with two scripted editors. The only
thing the tool controls per commit is the message text git hands to the
editor. Everything else (author, date, order) needs workarounds, and a
rebase can stop half way and leave interactive state behind.

A commit is a tree, parents, author, committer and message. Rewording keeps
the tree, so the tool can mint replacement commit objects with
`git commit-tree` and move `HEAD` once with `git update-ref`. No index, no
worktree, no rebase state. Atomic: the whole chain or nothing.

## How it works

1. Resolve the rewrite set. `rev-list --reverse --topo-order --parents
   <first>^..HEAD` (or `HEAD` alone when the first range commit is a root
   commit). Parents come before children, so a single pass works.
2. Walk it with a map `old sha -> new sha`. A commit is re-minted when it
   is changed, or when any of its parents is in the map. Otherwise it keeps
   its sha. Commits before the first change are therefore untouched, and
   side branches merged into HEAD that do not descend from a changed commit
   keep their shas too. Merges are re-minted like any commit, with every
   parent mapped; `commit-tree` takes several `-p`.
3. Mint: `git commit-tree <tree> -p <parent>... -F <message file>` with
   `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`, `GIT_AUTHOR_DATE` from the
   original commit. Committer is left to git: current user, now, same as a
   rebase gives. `commit.gpgsign` is honoured by `commit-tree`.
4. `git update-ref -m "reword: <n> commit(s)" HEAD <new tip> <old tip>`.
   `update-ref` follows the symref, so this updates the checked out branch
   or a detached HEAD alike. The old-value argument makes it fail if HEAD
   moved since the tool read it. The reflog entry gives `HEAD@{1}` and
   `<branch>@{1}` for undo, as after a rebase.

Nothing touches the index or the working tree. HEAD's tree is the same
tree as before, hanging off a different commit, so a dirty worktree is
fine and `git status` is unchanged afterwards.

Preconditions, checked before any object is written:

- Every range commit is in `<first>^..HEAD`, i.e. the range is on the
  current branch. Today this is assumed silently.
- HEAD resolves. Unborn branch is an error.

## Steps

1. **git.py.** `Commit` gains `tree: str` and `parents: list[str]`
   (`%T`, `%P` in `_LOG_FORMAT`). Dates: keep `--date=iso` for display;
   the same string round-trips through `GIT_AUTHOR_DATE` to the second and
   with the original offset. New helpers:
   - `commit_tree(tree, parents, message, *, author, author_date, cwd)`
     -> sha. Message goes through `-F` from a temp file (or stdin), with a
     trailing newline; `commit-tree` does no cleanup, `format.cleanup` is
     the only one, which is what the spec already says.
   - `update_ref(ref, new, old, *, message, cwd)`.
   - `rev_list_parents(range)` -> `[(sha, [parents])]`, oldest first.
2. **apply.py.** Delete `RebasePlan`, `plan_rebase`, `_sequence_editor`,
   `_message_editor`. Replace with:
   - `plan(commits) -> Plan(head, rewrite: list[(sha, parents)])` doing
     the precondition checks; raises `GitError`.
   - `apply(commits, edited) -> bool` as sketched above. Prints the count
     of re-minted commits, and how many were changed.
   `changed_commits` stays.
3. **cli.py.** `plan_rebase` becomes `apply_mod.plan`. The merge
   handling (`--rebase-merges` notice, `rebase.rebaseMerges` warning and
   confirmation) goes: merges are always kept. The root-commit error goes:
   a root commit is minted with no `-p`. The "Pre-reword HEAD / To revert"
   lines stay. `--continue` and `--force` are about the edit file and stay
   as they are; the "run `git rebase --abort`" text disappears with the
   message editor.
4. **Tests.** `tests/test_cli.py`:
   - Update: merge tests (`test_merge_preserved_and_reworded` stays and
     should pass unchanged; the two flatten tests go). Root commit test
     flips: rewording the root works.
   - New: dirty worktree is left alone and the reword succeeds; detached
     HEAD; commits after the range tip are re-minted with the same tree
     and message; commits before the first change keep their sha; author
     name, email and date are byte-identical after the rewrite (compare
     `%an %ae %ad` with `--date=raw`, and `%T`); a branch that moved
     under the tool makes `update-ref` fail and nothing else changes; a
     range not on HEAD's history is refused before anything is written.
   `tests/test_git.py`: `commit_tree` round-trips a message with `#`
   lines and a trailing blank line; `rev_list_parents` order.
5. **Docs.** README: the sentence about how changes are applied, and the
   `git reset --hard` undo hint gains `HEAD@{1}`. Spec "Reading back": the
   `commit.cleanup=whitespace` paragraph becomes "the tool writes commit
   objects directly with `commit-tree`; `cleanup` is the only cleanup".
   `prose/idea/reorder-commits.md`: note that the apply side is done and
   only the `merge-tree` step and the order semantics remain.

## Not doing

- Reordering. The engine is ready for it (the map handles moved parents),
  but the tree of a moved commit must come from `merge-tree --write-tree`
  and the spec must say order matters. Separate plan, see the idea file.
- Hooks. `rebase` ran `post-rewrite`; `commit-tree` runs nothing. Add a
  `post-rewrite` call later if someone has a hook that cares.
- A dirty-worktree check. Not needed while the tree never changes; needed
  again when reordering lands.
