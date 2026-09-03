"""Tests for git plumbing helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from git_reword import git
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
