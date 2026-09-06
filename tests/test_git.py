"""Tests for git plumbing helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from git_reword import git
from tests.conftest import MESSAGES
from tests.conftest import git as git_cmd


def test_get_commit_empty_message(repo: Path) -> None:
    git_cmd("commit", "-q", "--allow-empty-message", "--allow-empty", "-m", "", cwd=repo)

    commit = git.get_commit("HEAD", cwd=repo)
    assert commit.message == ""
    assert len(commit.sha) == 40
    assert commit.short

    commits = git.get_commits("HEAD~1..HEAD", cwd=repo)
    assert len(commits) == 1
    assert commits[0].sha == commit.sha
    assert commits[0].message == ""


def test_get_commits_matches_get_commit(repo: Path) -> None:
    """The one-log-call parser agrees with the per-commit one, field by field."""
    shas = git_cmd("rev-list", "--reverse", "HEAD", cwd=repo).split("\n")
    assert git.get_commits("HEAD", cwd=repo) == [git.get_commit(sha, cwd=repo) for sha in shas]

    commits = git.get_commits("HEAD~2..HEAD", cwd=repo)
    assert [c.message for c in commits] == MESSAGES[-2:]
    assert all(c.author == "Test <test@example.com>" and c.author_date for c in commits)
    assert all(c.committer == c.author and c.committer_date and c.short for c in commits)


def test_ident_validates_and_normalises(repo: Path) -> None:
    ident, date = git.ident(
        "author", " Some One  <some@one.example>", "@1771995569 +0100", cwd=repo
    )
    assert ident == "Some One <some@one.example>"
    assert date == "2026-02-25 05:59:29 +0100"
    _, same = git.ident("author", "A <a@b>", "2026-02-25 05:59:29 +0100", cwd=repo)
    assert same == date, "iso round-trips to the second, offset included"
    assert git.ident("committer", "C <c@d>", "@1771995569 +0100", cwd=repo) == ("C <c@d>", date)
    assert git.iso_date("1577914445 -0530") == "2020-01-01 16:04:05 -0530"
    with pytest.raises(git.GitError, match="invalid date"):
        git.ident("author", "A <a@b>", "bogus", cwd=repo)
    with pytest.raises(git.GitError, match="invalid date"):
        git.ident("committer", "A <a@b>", "bogus", cwd=repo)
    with pytest.raises(git.GitError, match="empty ident name"):
        git.ident("author", " <a@b>", None, cwd=repo)
    with pytest.raises(git.GitError, match="Name <email>"):
        git.ident("author", "no email", None, cwd=repo)


def test_get_commit_rejects_option_shaped_sha(repo: Path, tmp_path: Path) -> None:
    """--end-of-options keeps `git log` from reading the sha as an option."""
    out = tmp_path / "out"
    with pytest.raises(git.GitError):
        git.get_commit(f"--output={out}", cwd=repo)
    assert not out.exists()


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        ("git@gitlab.com:group/repo.git", "https://gitlab.com/group/repo"),
        ("git@github.com:user/repo", "https://github.com/user/repo"),
        (
            "ssh://git@gitlab.com/group/sub/repo.git",
            "https://gitlab.com/group/sub/repo",
        ),
        ("https://gitlab.com/group/repo.git", "https://gitlab.com/group/repo"),
        ("https://user@github.com/user/repo", "https://github.com/user/repo"),
        ("https://gitlab.com/group/repo/", "https://gitlab.com/group/repo"),
        ("/srv/git/repo.git", None),
        ("../other", None),
        ("file:///srv/git/repo.git", None),
    ],
)
def test_forge_url(remote: str, expected: str | None) -> None:
    assert git.forge_url(remote) == expected


def test_get_stats(repo: Path) -> None:
    """One Stat per non-merge commit: shortstat summary plus name-status files."""
    git_cmd("commit", "-q", "--allow-empty", "-m", "empty", cwd=repo)
    git_cmd("mv", "f0", "g0", cwd=repo)
    git_cmd("commit", "-q", "-m", "rename", cwd=repo)
    git_cmd("checkout", "-q", "-b", "side", "HEAD~2", cwd=repo)
    (repo / "s").write_text("s")
    git_cmd("add", "s", cwd=repo)
    git_cmd("commit", "-q", "-m", "side", cwd=repo)
    git_cmd("checkout", "-q", "main", cwd=repo)
    git_cmd("merge", "-q", "--no-ff", "-m", "merge", "side", cwd=repo)

    commits = git.get_commits("HEAD~4..HEAD", cwd=repo)
    stats = git.get_stats("HEAD~4..HEAD", cwd=repo)
    by_subject = {c.subject: stats.get(c.sha) for c in commits}
    assert by_subject == {
        "Third commit": git.Stat("1 file changed, 1 insertion(+)", [("A", "f3")]),
        "empty": git.Stat("", []),
        "rename": git.Stat("1 file changed, 0 insertions(+), 0 deletions(-)", [("R", "f0 -> g0")]),
        "side": git.Stat("1 file changed, 1 insertion(+)", [("A", "s")]),
        "merge": None,
    }


def test_commit_tree_round_trips_message_and_author(repo: Path) -> None:
    head = git.get_commit("HEAD", cwd=repo)
    assert head.tree and head.parents == [git_cmd("rev-parse", "HEAD~1", cwd=repo)]

    message = "Subject\n\n# a hash line\n\nbody"
    sha = git.commit_tree(
        head.tree,
        head.parents,
        message,
        author="Some One <some@one.example>",
        author_date="2026-02-25 05:59:29 +0100",
        cwd=repo,
    )
    minted = git.get_commit(sha, cwd=repo)
    assert minted.message == message
    assert minted.tree == head.tree and minted.parents == head.parents
    assert minted.author == "Some One <some@one.example>"
    assert git_cmd("log", "-1", "--format=%ad", "--date=raw", sha, cwd=repo) == "1771995569 +0100"
    # Committer comes from config, not from the author arguments...
    assert git_cmd("log", "-1", "--format=%cn", sha, cwd=repo) == "Test"
    # ...unless given.
    sha = git.commit_tree(
        head.tree,
        head.parents,
        message,
        author="Some One <some@one.example>",
        author_date="2026-02-25 05:59:29 +0100",
        committer="C D <c@d>",
        committer_date="2020-01-02 03:04:05 +0530",
        cwd=repo,
    )
    assert git_cmd("log", "-1", "--format=%cn <%ce>|%cd", "--date=raw", sha, cwd=repo) == (
        "C D <c@d>|1577914445 +0530"
    )
    # Nothing points at it: the repository is unchanged.
    assert git_cmd("rev-parse", "HEAD", cwd=repo) == head.sha


def test_commit_tree_root_and_bad_ident(repo: Path) -> None:
    tree = git_cmd("rev-parse", "HEAD^{tree}", cwd=repo)
    sha = git.commit_tree(tree, [], "root", author="A <a@b>", author_date="", cwd=repo)
    assert git.get_commit(sha, cwd=repo).parents == []
    with pytest.raises(git.GitError, match="Name <email>"):
        git.commit_tree(tree, [], "x", author="no email", author_date="", cwd=repo)


def test_history_orders_parents_first(repo: Path) -> None:
    shas = git_cmd("rev-list", "--reverse", "HEAD", cwd=repo).split("\n")
    assert [c.sha for c in git.history("HEAD", [shas[0]], cwd=repo)] == shas[1:]
    assert [c.sha for c in git.history("HEAD", [], cwd=repo)] == shas


def test_update_ref_follows_head_and_checks_old_value(repo: Path) -> None:
    old = git_cmd("rev-parse", "HEAD", cwd=repo)
    parent = git_cmd("rev-parse", "HEAD~1", cwd=repo)
    git.update_ref("HEAD", parent, old, message="test", cwd=repo)
    assert git_cmd("rev-parse", "main", cwd=repo) == parent
    assert git_cmd("symbolic-ref", "HEAD", cwd=repo) == "refs/heads/main"
    assert "test" in git_cmd("reflog", "-1", "main", cwd=repo)
    with pytest.raises(git.GitError):
        git.update_ref("HEAD", old, old, message="stale", cwd=repo)
