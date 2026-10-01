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
    """What a block asks for a commit: the message, and the info values the
    block carries, normalised the way git stores them (`Name <email>`, iso
    date). None means the line is absent (or the file is not in edit-info
    mode) and the commit gets git's default for it: the original author
    and author date, the current user and time as committer."""

    message: str
    author: str | None = None
    author_date: str | None = None
    committer: str | None = None
    committer_date: str | None = None

    def info_changes(self, commit: Commit) -> dict[str, tuple[str, str]]:
        """{key: (original, new)} for every info value that differs."""
        originals = fmt.info_values(commit)
        values = {
            "Author": self.author,
            "AuthorDate": self.author_date,
            "Commit": self.committer,
            "CommitDate": self.committer_date,
        }
        return {
            key: (originals[key], new)
            for key, new in values.items()
            if new is not None and new != originals[key]
        }

    def differs_from(self, commit: Commit) -> bool:
        return self.message != commit.message or bool(self.info_changes(commit))


def _normalise(key: str, value: str, commit: Commit, cwd: Path | str | None) -> str:
    """An edited info value as git would store it. Raises GitError."""
    role = "author" if key in fmt.AUTHOR_KEYS else "committer"
    if key.endswith("Date"):
        if not value:
            raise GitError("empty date")
        # The ident only has to be valid; the date is what is checked.
        probe = (commit.author if role == "author" else commit.committer) or "x <x@localhost>"
        return git.ident(role, probe, value, cwd=cwd)[1]
    name, email = git.split_ident(value)
    if not email.strip():
        raise GitError("empty email")
    if not name.strip():
        raise GitError("empty name")
    return git.ident(role, value, None, cwd=cwd)[0]


def block_edit(
    block: fmt.Block, commit: Commit, *, edit_info: bool, cwd: Path | str | None = None
) -> tuple[Edit, list[Diagnostic]]:
    """The edit a block describes for its commit, plus diagnostics on its
    info lines. An unknown key is an error. Without `edit_info` the lines
    are context: an edited one is a warning and the `Edit` carries only
    the message. With it every present line is on the `Edit`; edited
    values go through git for validation and normalisation, unchanged
    ones never reach git."""
    diagnostics: list[Diagnostic] = []
    originals = fmt.info_values(commit)
    values: dict[str, str | None] = dict.fromkeys(fmt.INFO_KEYS)

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
            if edit_info:
                values[key] = value
        elif not edit_info:
            diagnostics.append(
                Diagnostic(
                    line,
                    f"{key} is context only here, the edit is ignored; "
                    "rerun with --edit-info to apply it",
                    "info-display-only",
                    Severity.WARNING,
                )
            )
        else:
            try:
                values[key] = _normalise(key, value, commit, cwd)
            except GitError as e:
                code = "bad-" + ("author" if key in fmt.AUTHOR_KEYS else "committer")
                if key.endswith("Date"):
                    code += "-date"
                diagnostics.append(Diagnostic(line, f"Bad {key}: {e}", code))
    edit = Edit(
        block.message,
        author=values["Author"],
        author_date=values["AuthorDate"],
        committer=values["Commit"],
        committer_date=values["CommitDate"],
    )
    return edit, diagnostics


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


def _all_edits(
    changes: list[tuple[Commit, Edit]], edits: dict[str, Edit] | None
) -> dict[str, Edit]:
    return {**(edits or {}), **{c.sha: edit for c, edit in changes}}


def reminted(plan: Plan, changes: list[tuple[Commit, Edit]]) -> list[Commit]:
    """Every commit a rewrite writes anew, in history order: the changed
    ones and every commit with a re-minted parent."""
    changed = {c.sha for c, _ in changes}
    seen: set[str] = set()
    out: list[Commit] = []
    for commit in plan.history:
        if commit.sha in changed or any(p in seen for p in commit.parents):
            seen.add(commit.sha)
            out.append(commit)
    return out


@dataclass(frozen=True)
class Reference:
    """A sha in a re-minted commit's message naming an earlier re-minted
    commit (spec: reword-format.md, References to rewritten commits)."""

    commit: Commit  # the commit holding the reference
    start: int  # offsets of the token in the message it will get
    end: int
    token: str  # as written
    target: str  # the full old sha it names; for an ambiguous one, the first match


def references(
    plan: Plan, changes: list[tuple[Commit, Edit]], edits: dict[str, Edit] | None = None
) -> tuple[list[Reference], list[Reference]]:
    """(references to update, ambiguous ones), in history order. A token
    counts only against commits re-minted before the one holding it: a
    message can only name its ancestors."""
    edits = _all_edits(changes, edits)
    earlier: list[Commit] = []
    found: list[Reference] = []
    ambiguous: list[Reference] = []
    for commit in reminted(plan, changes):
        message = (edits.get(commit.sha) or Edit(commit.message)).message
        for m in fmt.SHA_REF_RE.finditer(message):
            targets = [c.sha for c in earlier if c.sha.startswith(m[0])]
            if targets:
                ref = Reference(commit, m.start(), m.end(), m[0], targets[0])
                (found if len(targets) == 1 else ambiguous).append(ref)
        earlier.append(commit)
    return found, ambiguous


def apply(
    plan: Plan,
    changes: list[tuple[Commit, Edit]],
    edits: dict[str, Edit] | None = None,
    *,
    rewrite_shas: bool = True,
) -> bool:
    """Re-mint the changed commits and their descendants, then move HEAD.
    `edits` are the blocks of every commit in the range (`changes` is the
    subset that differs): an unchanged block still says what its commit
    keeps when it is re-minted for a changed parent. With `rewrite_shas`,
    shas in the re-minted messages that name an earlier re-minted commit
    are updated to its new sha.

    All or nothing: the objects are written first and become reachable
    only with the final update-ref, which fails if HEAD moved meanwhile.
    """
    if not changes:
        return True

    edits = _all_edits(changes, edits)
    held: dict[str, list[Reference]] = {}
    if rewrite_shas:
        for ref in references(plan, changes, edits)[0]:
            held.setdefault(ref.commit.sha, []).append(ref)
    shorts: dict[tuple[str, int], str] = {}
    updated: list[str] = []

    mapped: dict[str, str] = {}
    for commit in reminted(plan, changes):
        edit = edits.get(commit.sha) or Edit(commit.message)
        message = edit.message
        replaced: list[tuple[str, str]] = []
        # From the end backwards, so earlier offsets stay valid.
        for ref in reversed(held.get(commit.sha, [])):
            new_sha = mapped[ref.target]
            if len(ref.token) == len(ref.target):
                token = new_sha
            else:
                key = (ref.target, len(ref.token))
                if key not in shorts:
                    shorts[key] = git.short(new_sha, len(ref.token))
                token = shorts[key]
            message = message[: ref.start] + token + message[ref.end :]
            replaced.append((ref.token, token))
        mapped[commit.sha] = git.commit_tree(
            commit.tree,
            [mapped.get(p, p) for p in commit.parents],
            message,
            author=edit.author or commit.author,
            author_date=edit.author_date or commit.author_date,
            committer=edit.committer,
            committer_date=edit.committer_date,
        )
        holder = mapped[commit.sha][:8]
        updated += [f"  {holder} {old} -> {new}" for old, new in reversed(replaced)]

    new_head = mapped.get(plan.head)
    if new_head is None:
        # Every change is in HEAD's history (plan checked), so HEAD is mapped.
        raise GitError("HEAD was not rewritten; this is a bug")

    n_changed, n_minted = len(changes), len(mapped)
    print(f"Rewrote {n_changed} commit(s), {n_minted - n_changed} descendant(s) re-minted")
    if updated:
        print("Updated shas of rewritten commits in messages:")
        print("\n".join(updated))
    git.update_ref("HEAD", new_head, plan.head, message=f"reword: {n_changed} commit(s)")
    print(f"HEAD is now {new_head[:8]} (was {plan.head[:8]}, see HEAD@{{1}})")
    return True
