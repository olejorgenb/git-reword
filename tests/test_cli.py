"""End-to-end: real git repo, real rebase, scripted editor."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

MESSAGES = [
    "Base",
    "First commit",
    "Second commit\n\n# a hash line that must survive\nBody text",
    "Third commit\n\nBody",
]


def git(*args: str, cwd: Path, **kw: object) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, **kw
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git("init", "-q", "-b", "main", cwd=tmp_path)
    git("config", "user.name", "Test", cwd=tmp_path)
    git("config", "user.email", "test@example.com", cwd=tmp_path)
    for i, message in enumerate(MESSAGES):
        (tmp_path / f"f{i}").write_text(str(i))
        git("add", ".", cwd=tmp_path)
        # --cleanup=verbatim so the `#` line is really in the history.
        git("commit", "-q", "--cleanup=verbatim", "-m", message, cwd=tmp_path)
    return tmp_path


def messages(repo: Path) -> list[str]:
    out = git("log", "--reverse", "--format=%B%x00", cwd=repo)
    return [m.strip("\n") for m in out.split("\x00") if m.strip()]


def run_reword(
    repo: Path, editor_script: str, *args: str, stdin: str = "y\n"
) -> subprocess.CompletedProcess[str]:
    script = repo / "editor.py"
    script.write_text(editor_script)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        [sys.executable, "-m", "git_reword.cli", *args, "--editor", f"{sys.executable} {script}"],
        cwd=repo,
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
    )


REPLACE_EDITOR = """\
import sys, pathlib
p = pathlib.Path(sys.argv[1])
text = p.read_text()
text = text.replace("    Second commit", "    Second commit, reworded")
text = text.replace("    Body\\n", "    Body\\n    # new hash line\\n")
p.write_text(text)
"""


def test_reword_preserves_hash_lines_and_rewrites_only_changed(repo: Path):
    before = git("rev-list", "HEAD", cwd=repo).split()
    result = run_reword(repo, REPLACE_EDITOR, "HEAD~3..HEAD")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Detected 2 changed commit(s)" in result.stdout

    assert messages(repo) == [
        "Base",
        "First commit",
        "Second commit, reworded\n\n# a hash line that must survive\nBody text",
        "Third commit\n\nBody\n# new hash line",
    ]
    after = git("rev-list", "HEAD", cwd=repo).split()
    assert after[2] == before[2], "unchanged first commit keeps its sha"
    assert not (repo / ".git" / "REWORD_EDITMSG").exists()


def test_no_changes(repo: Path):
    result = run_reword(repo, "pass", "HEAD~3..HEAD")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No changes detected" in result.stdout


def test_validation_error_keeps_file_and_reports_line(repo: Path):
    break_it = """\
import sys, pathlib
p = pathlib.Path(sys.argv[1])
p.write_text(p.read_text().replace("    Third commit", "  Third commit"))
"""
    result = run_reword(repo, break_it, "HEAD~3..HEAD")
    assert result.returncode == 1
    edit_file = repo / ".git" / "REWORD_EDITMSG"
    assert f"{edit_file}:" in result.stdout
    assert "error:" in result.stdout
    assert edit_file.exists()
    assert messages(repo) == MESSAGES

    # A second run refuses to clobber the file...
    result = run_reword(repo, "pass", "HEAD~3..HEAD")
    assert result.returncode == 1
    assert "--continue" in result.stdout

    # ...and --continue reopens it. The editor now fixes the indent.
    fix_it = """\
import sys, pathlib
p = pathlib.Path(sys.argv[1])
p.write_text(p.read_text().replace("  Third commit", "    Third commit (fixed)"))
"""
    result = run_reword(repo, fix_it, "HEAD~3..HEAD", "--continue")
    assert result.returncode == 0, result.stdout + result.stderr
    assert messages(repo)[3] == "Third commit (fixed)\n\nBody"


def test_single_commit_argument(repo: Path):
    result = run_reword(repo, REPLACE_EDITOR, "HEAD~1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert messages(repo)[2].startswith("Second commit, reworded")
    assert messages(repo)[3] == MESSAGES[3]
