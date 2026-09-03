"""End-to-end: real git repo, real rebase, scripted editor."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tests.conftest import MESSAGES, git, messages


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
    assert not (repo / "REWORD_EDITMSG").exists()


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
    edit_file = repo / "REWORD_EDITMSG"
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


EXCLUDE_EDITOR = """\
import sys, pathlib
p = pathlib.Path(sys.argv[1])
assert p.name == "REWORD_EDITMSG"
# The file must be ignored while the editor has it open.
import subprocess
subprocess.run(["git", "check-ignore", "-q", p.name], cwd=p.parent, check=True)
"""


def test_edit_file_is_excluded_once(repo: Path):
    exclude = repo / ".git" / "info" / "exclude"
    for _ in range(2):
        result = run_reword(repo, EXCLUDE_EDITOR, "HEAD~3..HEAD")
        assert result.returncode == 0, result.stdout + result.stderr
    assert exclude.read_text().splitlines().count("REWORD_EDITMSG") == 1
    assert "# added by git-reword" in exclude.read_text()


def test_gitignore_entry_leaves_exclude_alone(repo: Path):
    (repo / ".gitignore").write_text("REWORD_EDITMSG\n")
    git("add", ".gitignore", cwd=repo)
    git("commit", "-q", "-m", "ignore", cwd=repo)
    result = run_reword(repo, EXCLUDE_EDITOR, "HEAD~4..HEAD~1")
    assert result.returncode == 0, result.stdout + result.stderr
    exclude = repo / ".git" / "info" / "exclude"
    assert not exclude.exists() or "REWORD_EDITMSG" not in exclude.read_text()


def test_linked_worktree_uses_its_own_root(repo: Path, tmp_path_factory):
    linked = tmp_path_factory.mktemp("linked")
    git("worktree", "add", "-q", str(linked), "-b", "linked", cwd=repo)
    keep_it = """\
import sys, pathlib
p = pathlib.Path(sys.argv[1])
p.write_text(p.read_text().replace("    Third commit", "  Third commit"))
"""
    result = run_reword(linked, keep_it, "HEAD~3..HEAD")
    assert result.returncode == 1
    assert (linked / "REWORD_EDITMSG").exists()
    assert not (repo / "REWORD_EDITMSG").exists()
    assert "REWORD_EDITMSG" in (repo / ".git" / "info" / "exclude").read_text()


def test_single_commit_argument(repo: Path):
    result = run_reword(repo, REPLACE_EDITOR, "HEAD~1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert messages(repo)[2].startswith("Second commit, reworded")
    assert messages(repo)[3] == MESSAGES[3]
