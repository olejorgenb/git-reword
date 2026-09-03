"""Command line entry point."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer

from git_reword import apply as apply_mod
from git_reword import format as format_mod
from git_reword import git
from git_reword.git import Commit, GitError

EDIT_FILE = "REWORD_EDITMSG"


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").lower() not in ("", "0", "false", "no")


app = typer.Typer(
    add_completion=False,
    pretty_exceptions_show_locals=_env_truthy("DEBUG_SHOW_LOCALS"),
)


def validate(commits: list[Commit], result: format_mod.ParseResult, path: Path) -> bool:
    """Print every problem as path:line: message. True when clean."""
    ok = True
    for d in result.errors:
        print(f"{path}:{d.line + 1}: error: {d.message}")
        ok = False

    original = [c.sha for c in commits]
    edited = [b.sha for b in result.blocks]
    if missing := set(original) - set(edited):
        print(f"{path}: error: missing commits: {', '.join(s[:8] for s in sorted(missing))}")
        ok = False
    for b in result.blocks:
        if b.sha not in original:
            print(f"{path}:{b.line + 1}: error: unknown commit {b.sha[:8]}")
            ok = False
    if ok and edited != original:
        print(f"{path}: error: commits are not in the original order")
        ok = False
    return ok


def edit_file_path() -> Path:
    """`<worktree root>/REWORD_EDITMSG`, or under the git dir without a worktree."""
    root = git.toplevel()
    return (root if root is not None else git.git_dir()) / EDIT_FILE


EXCLUDE_MARKER = "# added by git-reword"


def ensure_excluded(edit_file: Path) -> None:
    """Make sure the edit file is ignored, via info/exclude in the common git dir.

    Leaves things alone when it is already ignored (e.g. by .gitignore) or
    lives under the git dir. Failure only warns: a stray untracked file is
    annoying, not dangerous.
    """
    try:
        if edit_file.is_relative_to(git.git_dir()):
            return
        if git.is_ignored(edit_file.name, cwd=edit_file.parent):
            return
        exclude = git.common_dir() / "info" / "exclude"
        existing = exclude.read_text() if exclude.exists() else ""
        if EDIT_FILE in existing.splitlines():
            return
        exclude.parent.mkdir(exist_ok=True)
        prefix = "" if existing == "" or existing.endswith("\n") else "\n"
        with exclude.open("a") as f:
            f.write(f"{prefix}{EXCLUDE_MARKER}\n{EDIT_FILE}\n")
    except (GitError, OSError) as e:
        print(f"Warning: could not add {EDIT_FILE} to info/exclude: {e}")


def open_editor(editor: str | None, path: Path) -> int:
    editor = editor or os.environ.get("EDITOR", "vim")
    cmd = shlex.split(editor) if " " in editor else [editor]
    return subprocess.run([*cmd, str(path)]).returncode


def reword(
    commit_range: str,
    *,
    editor: str | None,
    commit_link: bool,
    info: bool,
    continue_: bool,
    force: bool,
) -> bool:
    commits = git.get_commits(commit_range)
    if not commits:
        print("No commits found in the specified range")
        return False

    print(f"Found {len(commits)} commits to potentially reword")

    if len(commits) > 100:
        print(f"Warning: {len(commits)} commits is a lot. If this is unexpected, your")
        print("origin/HEAD symref may be stale — fix with: git remote set-head origin --auto")
        if input("Continue? [y/N] ").lower() != "y":
            print("Cancelled")
            return False

    edit_file = edit_file_path()
    if continue_:
        if not edit_file.exists():
            print(f"Error: nothing to continue, {edit_file} does not exist")
            return False
    else:
        if edit_file.exists() and edit_file.read_text().strip() and not force:
            print(f"Error: {edit_file} exists from an earlier run.")
            print("Use --continue to keep editing it, or --force to start over.")
            return False
        content = format_mod.write(
            commits, repo_url=git.repo_url(), commit_link=commit_link, info=info
        )
        ensure_excluded(edit_file)
        edit_file.write_text(content)

    keep = False
    try:
        if open_editor(editor, edit_file) != 0:
            print("Editor exited with error")
            keep = True
            return False

        result = format_mod.parse(edit_file.read_text())

        if not validate(commits, result, edit_file):
            keep = True
            return False

        changes = apply_mod.changed_commits(commits, result.messages)
        if not changes:
            print("No changes detected")
            return True

        print(f"\nDetected {len(changes)} changed commit(s):")
        for commit, new_msg in changes:
            print(f"\n{commit.sha[:8]}: {commit.subject}")
            print("  ↓")
            print(f"  {new_msg.split(chr(10))[0]}")
            if "\n" in new_msg:
                print("  (+ body changes)")

        while True:
            response = input("\nApply these changes? [Y/n] ").lower()
            if response == "n":
                print("Cancelled")
                keep = True
                return False
            if response in ("y", ""):
                break
            print("Invalid input")

        original_head = git.run("rev-parse", "HEAD")
        print(f"\nPre-reword HEAD: {original_head}")
        print(f"To revert:       git reset --hard {original_head}")

        success = apply_mod.apply(commits, result.messages)
        if not success:
            keep = True
        return success

    except KeyboardInterrupt:
        keep = True
        raise

    finally:
        if keep:
            print(f"Edits preserved at: {edit_file}")
            print("Rerun with --continue to pick up where you left off.")
        else:
            try:
                edit_file.unlink()
            except OSError:
                pass


@app.command()
def reword_command(
    commit_range: Annotated[
        str | None,
        typer.Argument(
            metavar="RANGE",
            help=(
                "Git commit range (HEAD~5..HEAD, main..feature) or a single commit. "
                "Defaults to all commits on the current branch."
            ),
        ),
    ] = None,
    editor: Annotated[
        str | None, typer.Option(help="Editor to use (defaults to $EDITOR or vim)")
    ] = None,
    commit_link: Annotated[
        bool, typer.Option("--commit-link", help="Add a forge commit URL comment per commit")
    ] = False,
    info: Annotated[
        bool, typer.Option("--info", help="Add Author and Date info lines per commit")
    ] = False,
    continue_: Annotated[
        bool,
        typer.Option("--continue", help="Reopen the edit file left by an earlier run"),
    ] = False,
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite an edit file left by an earlier run")
    ] = False,
) -> None:
    """Bulk edit git commit messages in your editor."""
    if not git.in_repo():
        print("Error: Not in a git repository")
        raise typer.Exit(1)

    try:
        if commit_range is None:
            commit_range = git.detect_branch_range()
        elif ".." not in commit_range:
            commit_range = f"{commit_range}^..{commit_range}"

        success = reword(
            commit_range,
            editor=editor,
            commit_link=commit_link,
            info=info,
            continue_=continue_,
            force=force,
        )
    except GitError as e:
        print(f"Error: {e}")
        raise typer.Exit(1) from None

    sys.exit(0 if success else 1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
