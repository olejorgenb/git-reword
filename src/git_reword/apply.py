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
from pathlib import Path

from git_reword import format as fmt
from git_reword import git
from git_reword.format import Diagnostic, Severity
from git_reword.git import Commit, GitError


@dataclass(frozen=True)
class Edit:
    """What a block asks for a commit: the message, and the author and
    author date when they were edited, normalised the way git stores them
    (`Name <email>`, iso date). None means keep the original."""

    message: str
    author: str | None = None
    author_date: str | None = None

    def differs_from(self, commit: Commit) -> bool:
        return (
            self.message != commit.message
            or self.author is not None
            or self.author_date is not None
        )


def block_edit(
    block: fmt.Block, commit: Commit, cwd: Path | str | None = None
) -> tuple[Edit, list[Diagnostic]]:
    """The edit a block describes for its commit, plus diagnostics on its
    info lines: an unknown key or a bad author value is an error, an edited
    committer line a warning (the committer of a rewritten commit is always
    the current user, now). Edited author values go through git for
    validation and normalisation; unchanged lines never reach git."""
    diagnostics: list[Diagnostic] = []
    originals = fmt.info_values(commit)
    author: str | None = None
    author_date: str | None = None

    for key, value in block.info.items():
        line = block.info_lines[key]
        if key not in fmt.INFO_KEYS:
            expected = ", ".join((*fmt.AUTHOR_KEYS, *fmt.COMMITTER_KEYS))
            diagnostics.append(
                Diagnostic(
                    line, f"Unknown info line `{key}`; expected {expected}", "unknown-info-key"
                )
            )
        elif value == originals[key]:
            continue
        elif key in fmt.COMMITTER_KEYS:
            diagnostics.append(
                Diagnostic(
                    line,
                    f"{key} is display only; a rewritten commit gets the current "
                    "committer and time",
                    "committer-edited",
                    Severity.WARNING,
                )
            )
        elif key == "Author":
            try:
                name, email = git.split_ident(value)
                if not email.strip():
                    raise GitError("empty email")
                if not name.strip():
                    raise GitError("empty name")
                author, _ = git.author_ident(value, None, cwd=cwd)
            except GitError as e:
                diagnostics.append(Diagnostic(line, f"Bad author: {e}", "bad-author"))
            else:
                if author == commit.author:
                    author = None
        else:  # AuthorDate
            try:
                if not value:
                    raise GitError("empty date")
                # The ident only has to be valid; the date is what is checked.
                probe = commit.author or "git-reword <reword@localhost>"
                _, author_date = git.author_ident(probe, value, cwd=cwd)
            except GitError as e:
                diagnostics.append(Diagnostic(line, f"Bad date: {e}", "bad-date"))
            else:
                if author_date == commit.author_date:
                    author_date = None
    return Edit(block.message, author, author_date), diagnostics


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


def changed_commits(commits: list[Commit], edits: dict[str, Edit]) -> list[tuple[Commit, Edit]]:
    """(commit, edit) for every commit whose message, author or author
    date differs."""
    changes = []
    for commit in commits:
        edit = edits.get(commit.sha)
        if edit is not None and edit.message and edit.differs_from(commit):
            changes.append((commit, edit))
    return changes


def apply(plan: Plan, changes: list[tuple[Commit, Edit]]) -> bool:
    """Re-mint the changed commits and their descendants, then move HEAD.

    All or nothing: the objects are written first and become reachable
    only with the final update-ref, which fails if HEAD moved meanwhile.
    """
    if not changes:
        return True

    edits = {c.sha: edit for c, edit in changes}
    mapped: dict[str, str] = {}
    for commit in plan.history:
        edit = edits.get(commit.sha)
        if edit is None and not any(p in mapped for p in commit.parents):
            continue
        mapped[commit.sha] = git.commit_tree(
            commit.tree,
            [mapped.get(p, p) for p in commit.parents],
            edit.message if edit else commit.message,
            author=edit.author if edit and edit.author else commit.author,
            author_date=edit.author_date if edit and edit.author_date else commit.author_date,
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
