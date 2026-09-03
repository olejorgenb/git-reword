from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

MESSAGES = [
    "Base",
    "First commit",
    "Second commit\n\n# a hash line that must survive\nBody text",
    "Third commit\n\nBody",
]


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def messages(repo: Path) -> list[str]:
    out = git("log", "--reverse", "--format=%B%x00", cwd=repo)
    return [m.strip("\n") for m in out.split("\x00") if m.strip()]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repo with the commits in MESSAGES and a GitLab-style origin."""
    git("init", "-q", "-b", "main", cwd=tmp_path)
    git("config", "user.name", "Test", cwd=tmp_path)
    git("config", "user.email", "test@example.com", cwd=tmp_path)
    git("remote", "add", "origin", "git@gitlab.com:group/repo.git", cwd=tmp_path)
    for i, message in enumerate(MESSAGES):
        (tmp_path / f"f{i}").write_text(str(i))
        git("add", ".", cwd=tmp_path)
        # --cleanup=verbatim so the `#` line is really in the history.
        git("commit", "-q", "--cleanup=verbatim", "-m", message, cwd=tmp_path)
    return tmp_path
