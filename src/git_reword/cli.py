"""Command line entry point."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
from typing import Annotated

import typer

from git_reword import apply as apply_mod
from git_reword import format as format_mod
from git_reword import git
from git_reword.git import Commit, GitError


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").lower() not in ("", "0", "false", "no")


app = typer.Typer(
    add_completion=False,
    pretty_exceptions_show_locals=_env_truthy("DEBUG_SHOW_LOCALS"),
)


def validate(commits: list[Commit], edited: dict[str, str]) -> bool:
    original_shas = {c.sha for c in commits}
    edited_shas = set(edited)
    if original_shas == edited_shas:
        return True
    if missing := original_shas - edited_shas:
        print(f"Error: Missing commits in edited file: {missing}")
    if extra := edited_shas - original_shas:
        print(f"Error: Unknown commits in edited file: {extra}")
    return False


def open_editor(editor: str | None, path: str) -> int:
    editor = editor or os.environ.get("EDITOR", "vim")
    cmd = shlex.split(editor) if " " in editor else [editor]
    return subprocess.run([*cmd, path]).returncode


def reword(commit_range: str, editor: str | None, commit_link: bool) -> bool:
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

    content = format_mod.write(commits, repo_url=git.repo_url(), commit_link=commit_link)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(content)
        temp_file = f.name

    keep_temp = False
    try:
        if open_editor(editor, temp_file) != 0:
            print("Editor exited with error")
            keep_temp = True
            return False

        with open(temp_file) as f:
            edited = format_mod.parse(f.read())

        if not validate(commits, edited):
            keep_temp = True
            return False

        changes = apply_mod.changed_commits(commits, edited)
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
                keep_temp = True
                return False
            if response in ("y", ""):
                break
            print("Invalid input")

        original_head = git.run("rev-parse", "HEAD")
        print(f"\nPre-reword HEAD: {original_head}")
        print(f"To revert:       git reset --hard {original_head}")

        success = apply_mod.apply(commits, edited)
        if not success:
            keep_temp = True
        return success

    except KeyboardInterrupt:
        keep_temp = True
        raise

    finally:
        if keep_temp:
            print(f"Edits preserved at: {temp_file}")
        else:
            try:
                os.unlink(temp_file)
            except OSError:
                pass


@app.command()
def main(
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
    no_commit_link: Annotated[
        bool, typer.Option("--no-commit-link", help="Omit forge commit URL comments")
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

        success = reword(commit_range, editor, commit_link=not no_commit_link)
    except GitError as e:
        print(f"Error: {e}")
        raise typer.Exit(1) from None

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    app()
