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
    assert all(c.author == "Test <test@example.com>" and c.date and c.short for c in commits)


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
