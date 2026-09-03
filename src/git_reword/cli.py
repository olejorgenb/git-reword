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


def resolve(
    commits: list[Commit], result: format_mod.ParseResult, path: Path
) -> dict[str, str] | None:
    """Match each block to a commit of the range by sha prefix and return
    {full sha: message}. Prints every problem as path:line: message and
    returns None when there is any. Never asks git: the range is the only
    valid universe for the file's shas."""
    ok = True
    for d in result.errors:
        print(f"{path}:{d.line + 1}: error: {d.message}")
        ok = False

    messages: dict[str, str] = {}
    edited: list[str] = []
    for b in result.blocks:
        matches = [c for c in commits if c.sha.startswith(b.sha)]
        if not matches:
            print(f"{path}:{b.line + 1}: error: unknown commit {b.sha[:8]}")
            ok = False
        elif len(matches) > 1:
            print(f"{path}:{b.line + 1}: error: ambiguous sha {b.sha}")
            ok = False
        elif matches[0].sha in messages:
            print(f"{path}:{b.line + 1}: error: duplicate commit {b.sha[:8]}")
            ok = False
        else:
            messages[matches[0].sha] = b.message
            edited.append(matches[0].sha)

    original = [c.sha for c in commits]
    if missing := set(original) - set(edited):
        print(f"{path}: error: missing commits: {', '.join(s[:8] for s in sorted(missing))}")
        ok = False
    if ok and edited != original:
        print(f"{path}: error: commits are not in the original order")
        ok = False
    return messages if ok else None


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


def confirm(prompt: str, *, default: bool) -> bool:
    """Ask a y/n question. Enter picks the default; EOF (no terminal) means no."""
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        try:
            response = input(f"{prompt} {suffix} ").strip().lower()
        except EOFError:
            return False
        if response == "":
            return default
        if response in ("y", "yes"):
            return True
        if response in ("n", "no"):
            return False
        print("Invalid input")


def reword(
    commit_range: str,
    *,
    editor: str | None,
    commit_link: bool,
    info: bool,
    abbrev: bool,
    continue_: bool,
    force: bool,
) -> bool:
    commits = git.get_commits(commit_range)
    if not commits:
        print("No commits found in the specified range")
        return False

    # A GitError here (root commit as the first commit of the range)
    # propagates to reword_command, which prints it and exits 1.
    plan = apply_mod.plan_rebase(commits)

    print(f"Found {len(commits)} commits to potentially reword")

    if len(commits) > 100:
        print(f"Warning: {len(commits)} commits is a lot. If this is unexpected, your")
        print("origin/HEAD symref may be stale — fix with: git remote set-head origin --auto")
        if not confirm("Continue?", default=False):
            print("Cancelled")
            return False

    if plan.merges:
        if plan.rebase_merges:
            print(
                f"{len(plan.merges)} merge commit(s) in {plan.base[:8]}..HEAD; "
                "rebasing with --rebase-merges to keep them"
            )
        else:
            print(
                f"Warning: rebase.rebaseMerges is false; this rebase will flatten "
                f"{len(plan.merges)} merge commit(s)."
            )
            if not confirm("Continue?", default=False):
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
            commits, repo_url=git.repo_url(), commit_link=commit_link, info=info, abbrev=abbrev
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

        messages = resolve(commits, result, edit_file)
        if messages is None:
            keep = True
            return False

        changes = apply_mod.changed_commits(commits, messages)
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

        if not confirm("\nApply these changes?", default=True):
            print("Cancelled")
            keep = True
            return False

        original_head = git.run("rev-parse", "HEAD")
        print(f"\nPre-reword HEAD: {original_head}")
        print(f"To revert:       git reset --hard {original_head}")

        success = apply_mod.apply(commits, messages)
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
    abbrev: Annotated[
        bool,
        typer.Option("--abbrev/--no-abbrev", help="Abbreviated or full shas on commit lines"),
    ] = True,
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
            abbrev=abbrev,
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
