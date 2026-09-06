"""Apply new commit messages by writing commit objects directly.

A commit is a tree, parents, author, committer and message. Rewording keeps
the tree, so every changed commit and every descendant of one is re-minted
with `git commit-tree`, and HEAD is moved once with `git update-ref`. The
index and the working tree are never touched: HEAD's tree is the same tree
afterwards, hanging off a different commit. See
prose/plan/2026-09-06/commit-tree-apply.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from git_reword import git
from git_reword.git import Commit, GitError


@dataclass(frozen=True)
class Plan:
    head: str  # HEAD when the plan was made; update-ref insists it is still there
    history: list[Commit]  # every commit a rewrite may touch, parents before children


def plan(commits: list[Commit]) -> Plan:
    """Check that the range can be rewritten under HEAD and fetch the
    commits a rewrite may touch: the range, plus everything reachable from
    HEAD that is not reachable from a parent outside the range. Commits in
    there that do not descend from a changed commit keep their sha.

    Raises GitError when HEAD is unborn or a range commit is not in HEAD's
    history.
    """
    try:
        head = git.run("rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    except GitError:
        raise GitError("HEAD does not point at a commit") from None

    in_range = {c.sha for c in commits}
    outside_parents = sorted({p for c in commits for p in c.parents if p not in in_range})
    history = git.history("HEAD", outside_parents)

    walked = {c.sha for c in history}
    if unreachable := [c for c in commits if c.sha not in walked]:
        shas = ", ".join(c.sha[:8] for c in unreachable)
        raise GitError(f"not in HEAD's history, cannot rewrite: {shas}")
    return Plan(head=head, history=history)


def changed_commits(commits: list[Commit], edited: dict[str, str]) -> list[tuple[Commit, str]]:
    """(commit, new message) for every commit whose message differs."""
    changes = []
    for commit in commits:
        new_message = edited.get(commit.sha, "")
        if new_message and new_message != commit.message:
            changes.append((commit, new_message))
    return changes


def apply(plan: Plan, changes: list[tuple[Commit, str]]) -> bool:
    """Re-mint the changed commits and their descendants, then move HEAD.

    All or nothing: the objects are written first and become reachable
    only with the final update-ref, which fails if HEAD moved meanwhile.
    """
    if not changes:
        return True

    new_messages = {c.sha: message for c, message in changes}
    mapped: dict[str, str] = {}
    for commit in plan.history:
        if commit.sha not in new_messages and not any(p in mapped for p in commit.parents):
            continue
        mapped[commit.sha] = git.commit_tree(
            commit.tree,
            [mapped.get(p, p) for p in commit.parents],
            new_messages.get(commit.sha, commit.message),
            author=commit.author,
            author_date=commit.date,
        )

    new_head = mapped.get(plan.head)
    if new_head is None:
        # Every change is in HEAD's history (plan checked), so HEAD is mapped.
        raise GitError("HEAD was not rewritten; this is a bug")

    n_changed, n_minted = len(changes), len(mapped)
    print(f"Rewrote {n_changed} commit(s), {n_minted - n_changed} descendant(s) re-minted")
    git.update_ref("HEAD", new_head, plan.head, message=f"reword: {n_changed} commit(s)")
    print(f"HEAD is now {new_head[:8]} (was {plan.head[:8]}, see HEAD@{{1}})")
    return True
