"""Tests for git plumbing helpers."""

from __future__ import annotations

from pathlib import Path

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
