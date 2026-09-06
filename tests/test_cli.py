"""End-to-end: real git repo, real commit objects, scripted editor."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from git_reword.apply import Edit
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


def test_eof_on_prompt_cancels(repo: Path):
    """No terminal on stdin: the confirm prompt treats EOF as no, not a crash."""
    result = run_reword(repo, REPLACE_EDITOR, "HEAD~3..HEAD", stdin="")
    assert result.returncode == 1
    assert "Cancelled" in result.stdout
    assert messages(repo) == MESSAGES


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


def test_abbrev_default_and_no_abbrev(repo: Path):
    """The default file has short shas and still rewords; --no-abbrev has full ones."""
    capture = """\
import sys, pathlib, shutil
p = pathlib.Path(sys.argv[1])
shutil.copy(p, p.with_name("captured"))
"""
    result = run_reword(repo, capture, "HEAD~3..HEAD")
    assert result.returncode == 0, result.stdout + result.stderr
    shas = [
        ln.split()[1]
        for ln in (repo / "captured").read_text().splitlines()
        if ln.startswith("commit ")
    ]
    assert shas and all(7 <= len(s) < 40 for s in shas)

    result = run_reword(repo, capture, "--no-abbrev", "HEAD~3..HEAD")
    assert result.returncode == 0, result.stdout + result.stderr
    shas = [
        ln.split()[1]
        for ln in (repo / "captured").read_text().splitlines()
        if ln.startswith("commit ")
    ]
    assert shas and all(len(s) == 40 for s in shas)

    result = run_reword(repo, REPLACE_EDITOR, "HEAD~3..HEAD")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Detected 2 changed commit(s)" in result.stdout


def test_duplicate_original_message_gets_own_new_message(repo: Path):
    """Two commits sharing an original message must not collapse to one new message."""
    git("commit", "-q", "--allow-empty", "-m", "wip", cwd=repo)
    git("commit", "-q", "--allow-empty", "-m", "wip", cwd=repo)

    editor = """\
import sys, pathlib
p = pathlib.Path(sys.argv[1])
text = p.read_text()
text = text.replace("    wip\\n", "    X\\n", 1)
text = text.replace("    wip\\n", "    Y\\n", 1)
p.write_text(text)
"""
    result = run_reword(repo, editor, "HEAD~2..HEAD")
    assert result.returncode == 0, result.stdout + result.stderr
    assert messages(repo)[-2:] == ["X", "Y"]


EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

MERGE_EDITOR = """\
import sys, pathlib
p = pathlib.Path(sys.argv[1])
text = p.read_text()
text = text.replace("    Feature one\\n", "    Feature one, reworded\\n")
text = text.replace(
    "    Merge main into feature\\n", "    Merge main into feature, reworded\\n"
)
p.write_text(text)
"""


def _make_feature_merge(repo: Path) -> None:
    """feature, branched at HEAD~1 (Second commit): Feature one, then a
    --no-ff merge of main (Third commit) in, then Feature two."""
    git("checkout", "-q", "-b", "feature", "HEAD~1", cwd=repo)
    (repo / "f-one").write_text("one")
    git("add", ".", cwd=repo)
    git("commit", "-q", "-m", "Feature one", cwd=repo)
    git("checkout", "-q", "feature", cwd=repo)
    git("merge", "-q", "--no-ff", "main", "-m", "Merge main into feature", cwd=repo)
    (repo / "f-two").write_text("two")
    git("add", ".", cwd=repo)
    git("commit", "-q", "-m", "Feature two", cwd=repo)


def test_merge_preserved_and_reworded(repo: Path):
    _make_feature_merge(repo)
    main_sha = git("rev-parse", "main", cwd=repo)

    result = run_reword(repo, MERGE_EDITOR, "main..HEAD")
    assert result.returncode == 0, result.stdout + result.stderr

    assert git("rev-parse", "main", cwd=repo) == main_sha
    merges = git("rev-list", "--merges", "main..HEAD", cwd=repo).split()
    assert len(merges) == 1
    assert (
        git("log", "-1", "--format=%s", "HEAD~1", cwd=repo) == "Merge main into feature, reworded"
    )
    assert git("rev-parse", "HEAD~1^2", cwd=repo) == main_sha
    assert git("log", "-1", "--format=%s", "HEAD~2", cwd=repo) == "Feature one, reworded"
    assert git("log", "-1", "--format=%s", "HEAD", cwd=repo) == "Feature two"
    assert git("status", "--porcelain", "-uno", cwd=repo) == ""


ROOT_EDITOR = """\
import sys, pathlib
p = pathlib.Path(sys.argv[1])
p.write_text(p.read_text().replace("    Base\\n", "    Base, reworded\\n"))
"""


def test_root_commit_reworded(repo: Path):
    # ".."/no-terminal defaults empty side to HEAD (HEAD..HEAD, empty range),
    # not to the root; the well-known empty-tree sha is the idiom that
    # actually yields the full history, root commit included.
    result = run_reword(repo, ROOT_EDITOR, f"{EMPTY_TREE}..HEAD")
    assert result.returncode == 0, result.stdout + result.stderr
    assert messages(repo) == ["Base, reworded", *MESSAGES[1:]]
    root = git("rev-list", "--max-parents=0", "HEAD", cwd=repo)
    assert git("log", "-1", "--format=%P", root, cwd=repo) == ""


def _idents(repo: Path, rev: str = "HEAD") -> list[str]:
    """Author name, email, raw date and tree per commit: what a reword must keep."""
    return git("log", "--format=%an|%ae|%ad|%T", "--date=raw", rev, cwd=repo).split("\n")


def test_dirty_worktree_and_staged_changes_are_left_alone(repo: Path):
    (repo / "f1").write_text("dirty")
    (repo / "staged").write_text("staged")
    git("add", "staged", cwd=repo)
    status = git("status", "--porcelain", "-uno", cwd=repo)
    idents = _idents(repo)

    result = run_reword(repo, REPLACE_EDITOR, "HEAD~3..HEAD")
    assert result.returncode == 0, result.stdout + result.stderr
    assert git("status", "--porcelain", "-uno", cwd=repo) == status
    assert (repo / "f1").read_text() == "dirty"
    assert _idents(repo) == idents


def test_commits_after_the_range_are_carried_along(repo: Path):
    """HEAD is two commits past the range tip: those are re-minted onto the
    new parents with everything but their sha intact; commits before the
    first change keep their sha."""
    before = git("rev-list", "HEAD", cwd=repo).split()
    idents = _idents(repo)

    result = run_reword(repo, REPLACE_EDITOR, "HEAD~3..HEAD~1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Detected 1 changed commit(s)" in result.stdout
    assert messages(repo)[2].startswith("Second commit, reworded")
    assert messages(repo)[3] == MESSAGES[3]
    after = git("rev-list", "HEAD", cwd=repo).split()
    assert after[0] != before[0], "HEAD descends from a changed commit, so it moved"
    assert after[2:] == before[2:], "commits before the first change keep their sha"
    assert _idents(repo) == idents
    assert git("rev-parse", "HEAD@{1}", cwd=repo) == before[0]


def test_detached_head(repo: Path):
    git("checkout", "-q", "--detach", cwd=repo)
    main_sha = git("rev-parse", "main", cwd=repo)
    result = run_reword(repo, REPLACE_EDITOR, "HEAD~3..HEAD")
    assert result.returncode == 0, result.stdout + result.stderr
    assert git("rev-parse", "main", cwd=repo) == main_sha, "the branch is not touched"
    assert git("rev-parse", "HEAD", cwd=repo) != main_sha
    assert messages(repo)[2].startswith("Second commit, reworded")


def test_head_moved_during_edit_is_refused(repo: Path):
    """The editor commits meanwhile: update-ref sees a stale old value and
    nothing changes."""
    editor = f"""\
import sys, pathlib, subprocess
p = pathlib.Path(sys.argv[1])
subprocess.run(
    ["git", "commit", "-q", "--allow-empty", "-m", "sneaky"], cwd={str(repo)!r}, check=True
)
p.write_text(p.read_text().replace("    Third commit", "    Third commit, reworded"))
"""
    result = run_reword(repo, editor, "HEAD~3..HEAD")
    assert result.returncode == 1
    assert "Error:" in result.stdout
    assert messages(repo) == [*MESSAGES, "sneaky"]
    assert (repo / "REWORD_EDITMSG").exists()


def test_range_not_under_head_is_refused(repo: Path):
    git("branch", "other", "HEAD", cwd=repo)
    git("checkout", "-q", "HEAD~2", cwd=repo)
    result = run_reword(repo, "pass", "other~2..other")
    assert result.returncode == 1
    assert "not in HEAD's history" in result.stdout
    assert "Traceback" not in result.stderr
    assert not (repo / "REWORD_EDITMSG").exists()


def _resolver(tmp_path: Path):
    """resolve() over two commits sharing the prefix aaaa, printing to capsys."""
    from git_reword import format as fmt
    from git_reword.cli import resolve
    from git_reword.git import Commit

    a = Commit("aaaa1111" + "0" * 32, "A")
    b = Commit("aaaa2222" + "0" * 32, "B")
    path = tmp_path / "REWORD_EDITMSG"

    def run(text: str, capsys) -> tuple[dict[str, Edit] | None, str]:
        edits = resolve([a, b], fmt.parse(text), path)
        return edits, capsys.readouterr().out

    return run


def test_resolve_cases(tmp_path: Path, capsys):
    """Prefix resolution: ok, ambiguous, unknown and missing, duplicate, order."""
    run = _resolver(tmp_path)
    ok, out = run("commit aaaa1111\n    A\ncommit aaaa2222\n    B2\n", capsys)
    assert ok == {"aaaa1111" + "0" * 32: Edit("A"), "aaaa2222" + "0" * 32: Edit("B2")}
    assert out == ""

    none, out = run("commit aaaa\n    A\ncommit aaaa2222\n    B\n", capsys)
    assert none is None and "ambiguous sha aaaa" in out

    none, out = run("commit bbbb\n    A\ncommit aaaa2222\n    B\n", capsys)
    assert none is None and "unknown commit bbbb" in out and "missing commits: aaaa1111" in out

    none, out = run("commit aaaa1111\n    A\ncommit aaaa11110\n    B\n", capsys)
    assert none is None and "duplicate commit aaaa1111" in out

    none, out = run("commit aaaa2222\n    B\ncommit aaaa1111\n    A\n", capsys)
    assert none is None and "not in the original order" in out


def test_resolve_malformed_sha_reported_once(tmp_path: Path, capsys):
    """A sha the parser rejected is not also reported as unknown."""
    run = _resolver(tmp_path)
    none, out = run("commit zzzz\n    A\ncommit aaaa2222\n    B\n", capsys)
    assert none is None
    assert "Not a sha" in out
    assert "missing commits: aaaa1111" in out
    assert "unknown commit" not in out


def test_message_diff_shows_only_changed_lines():
    from git_reword.cli import message_diff

    old = "Subject\n\nBody line one\nBody line two\nBody line three"
    assert message_diff(old, "New subject\n\nBody line one\nBody line two\nBody line three") == (
        "  -Subject\n  +New subject\n   "
    )
    assert message_diff(old, "Subject\n\nBody line one\nChanged\nBody line three") == (
        "   Body line one\n  -Body line two\n  +Changed\n   Body line three"
    )
    # Two far-apart hunks are separated by a blank line, not @@ markers.
    both = message_diff(old, "New subject\n\nBody line one\nBody line two\nChanged")
    assert both == (
        "  -Subject\n  +New subject\n   \n\n   Body line two\n  -Body line three\n  +Changed"
    )
    assert "@@" not in both


def test_message_diff_color_marks_only_changed_lines():
    from git_reword.cli import message_diff

    out = message_diff("A\nB", "A\nC", color=True)
    assert out == "   A\n  \033[31m-B\033[0m\n  \033[32m+C\033[0m"


def test_stat_writes_foldable_comments(repo: Path):
    copy = repo / "copy"
    editor = f"import sys, shutil; shutil.copy(sys.argv[1], {str(copy)!r})"
    result = run_reword(repo, editor, "HEAD~2..HEAD", "--stat")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No changes detected" in result.stdout
    text = copy.read_text()
    assert "\n    Body\n\n#   1 file changed, 1 insertion(+)\n#   A  f3\n" in text
    assert "#   A  f2\n" in text

    plain = run_reword(repo, editor, "HEAD~2..HEAD")
    assert plain.returncode == 0
    assert "files changed" not in copy.read_text()


def _replace_editor(old: str, new: str, count: int = -1) -> str:
    return f"""\
import sys, pathlib
p = pathlib.Path(sys.argv[1])
p.write_text(p.read_text().replace({old!r}, {new!r}, {count}))
"""


def _authors(repo: Path) -> list[str]:
    return git("log", "--reverse", "--format=%an <%ae>", cwd=repo).split("\n")


def test_author_search_and_replace(repo: Path):
    """The use case: every commit in the range was made with the wrong
    identity; one replace fixes them all, messages and dates untouched."""
    before = _idents(repo)
    editor = _replace_editor("Test <test@example.com>", "Ole <ole@example.com>")
    result = run_reword(repo, editor, "HEAD~3..HEAD", "--author-info")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Detected 3 changed commit(s)" in result.stdout
    assert "  Author:     Test <test@example.com> -> Ole <ole@example.com>" in result.stdout
    assert "AuthorDate" not in result.stdout

    assert _authors(repo) == ["Test <test@example.com>"] + ["Ole <ole@example.com>"] * 3
    assert messages(repo) == MESSAGES
    after = _idents(repo)
    assert [i.split("|")[2:] for i in after] == [i.split("|")[2:] for i in before], "dates, trees"
    assert git("log", "--format=%cn", "-1", cwd=repo) == "Test", "committer is still config"


def test_author_date_round_trips(repo: Path):
    original = git("log", "-1", "--format=%ad", "--date=iso", cwd=repo)
    editor = _replace_editor(f"AuthorDate: {original}", "AuthorDate: 2020-01-02 03:04:05 +0530")
    result = run_reword(repo, editor, "HEAD~1..HEAD", "--author-info")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"  AuthorDate: {original} -> 2020-01-02 03:04:05 +0530" in result.stdout
    assert git("log", "-1", "--format=%ad", "--date=raw", cwd=repo) == "1577914445 +0530"
    assert git("log", "-1", "--format=%an", cwd=repo) == "Test"
    assert messages(repo) == MESSAGES


def test_bad_author_values_are_reported_with_lines(repo: Path):
    original = git("log", "-1", "--format=%ad", "--date=iso", cwd=repo)
    editor = f"""\
import sys, pathlib
p = pathlib.Path(sys.argv[1])
text = p.read_text()
text = text.replace("Author:     Test <test@example.com>", "Author:     Test", 1)
text = text.replace("AuthorDate: {original}", "AuthorDate: bogus")
p.write_text(text)
"""
    before = git("rev-parse", "HEAD", cwd=repo)
    result = run_reword(repo, editor, "HEAD~2..HEAD", "--author-info")
    assert result.returncode == 1
    edit_file = repo / "REWORD_EDITMSG"
    lines = edit_file.read_text().split("\n")
    author_line = lines.index("Author:     Test") + 1
    date_line = lines.index("AuthorDate: bogus") + 1
    assert f"{edit_file}:{author_line}: error: Bad author: not a `Name <email>`" in result.stdout
    assert f"{edit_file}:{date_line}: error: Bad date: invalid date format" in result.stdout
    assert git("rev-parse", "HEAD", cwd=repo) == before


def test_edited_committer_warns_and_proceeds(repo: Path):
    """A replace on the author name also hits the Commit: line; that is a
    warning, and the author change still goes through."""
    editor = _replace_editor("Test <test@example.com>", "Ole <ole@example.com>")
    result = run_reword(repo, editor, "HEAD~2..HEAD", "--author-info", "--commit-info")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("warning: Commit is display only") == 2
    assert "CommitDate" not in result.stdout
    assert _authors(repo)[-2:] == ["Ole <ole@example.com>"] * 2
    assert git("log", "--format=%cn", "-1", cwd=repo) == "Test"


def test_unknown_info_key_is_error(repo: Path):
    editor = _replace_editor("Author:", "Autor:", 1)
    result = run_reword(repo, editor, "HEAD~2..HEAD", "--author-info")
    assert result.returncode == 1
    assert "error: Unknown info line `Autor`; expected Author, AuthorDate" in result.stdout
    assert (repo / "REWORD_EDITMSG").exists()


def test_unchanged_and_reformatted_info_lines_are_not_changes(repo: Path):
    result = run_reword(repo, "pass", "HEAD~3..HEAD", "--author-info", "--commit-info")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No changes detected" in result.stdout

    # Whitespace git would strip anyway, and a date in another notation.
    original = git("log", "-1", "--format=%ad", "--date=iso", cwd=repo)
    raw = git("log", "-1", "--format=%ad", "--date=raw", cwd=repo)
    editor = f"""\
import sys, pathlib
p = pathlib.Path(sys.argv[1])
text = p.read_text()
text = text.replace("Author:     Test <test@example.com>", "Author:  Test   <test@example.com>")
text = text.replace("AuthorDate: {original}", "AuthorDate: @{raw}")
p.write_text(text)
"""
    result = run_reword(repo, editor, "HEAD~1..HEAD", "--author-info")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No changes detected" in result.stdout
