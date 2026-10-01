"""Command line entry point."""

from __future__ import annotations

import difflib
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
) -> dict[str, apply_mod.Edit] | None:
    """Match each block to a commit of the range by sha prefix and return
    {full sha: Edit}. Prints every problem as path:line: message and
    returns None when there is any error; warnings (an edited committer
    line) are printed and do not stop the run. Sha resolution never asks
    git: the range is the only valid universe for the file's shas. Edited
    author values are validated through git."""
    ok = True
    for d in result.errors:
        print(f"{path}:{d.line + 1}: error: {d.message}")
        ok = False

    edits: dict[str, apply_mod.Edit] = {}
    edited: list[str] = []
    # A sha the parser rejected is already reported; do not call it unknown too.
    bad = {d.line for d in result.diagnostics if d.code == "bad-sha"}
    for b in result.blocks:
        if b.line in bad:
            continue
        matches = [c for c in commits if c.sha.startswith(b.sha)]
        if not matches:
            print(f"{path}:{b.line + 1}: error: unknown commit {b.sha[:8]}")
            ok = False
        elif len(matches) > 1:
            print(f"{path}:{b.line + 1}: error: ambiguous sha {b.sha}")
            ok = False
        elif matches[0].sha in edits:
            print(f"{path}:{b.line + 1}: error: duplicate commit {b.sha[:8]}")
            ok = False
        else:
            edit, diagnostics = apply_mod.block_edit(b, matches[0], edit_info=result.edit_info)
            for d in diagnostics:
                print(f"{path}:{d.line + 1}: {d.severity.value}: {d.message}")
                ok = ok and d.severity is not format_mod.Severity.ERROR
            edits[matches[0].sha] = edit
            edited.append(matches[0].sha)

    original = [c.sha for c in commits]
    if missing := set(original) - set(edited):
        print(f"{path}: error: missing commits: {', '.join(s[:8] for s in sorted(missing))}")
        ok = False
    if ok and edited != original:
        print(f"{path}: error: commits are not in the original order")
        ok = False
    return edits if ok else None


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


_RED, _GREEN, _RESET = "\033[31m", "\033[32m", "\033[0m"


def message_diff(old: str, new: str, *, color: bool = False) -> str:
    """Unified diff of two messages, indented, without file headers.

    One line of context; hunks separated by a blank line rather than `@@`
    markers, which say nothing useful for a text this short.
    """
    lines = difflib.unified_diff(old.split("\n"), new.split("\n"), n=1, lineterm="")
    out: list[str] = []
    for line in list(lines)[2:]:  # skip the ---/+++ headers
        if line.startswith("@@"):
            if out:
                out.append("")
            continue
        if color and line[0] in "-+":
            line = f"{_RED if line[0] == '-' else _GREEN}{line}{_RESET}"
        out.append(f"  {line}")
    return "\n".join(out)


def print_references(
    plan: apply_mod.Plan,
    changes: list[tuple[Commit, apply_mod.Edit]],
    edits: dict[str, apply_mod.Edit],
) -> None:
    """List the shas of rewritten commits named in messages: the holder,
    the token as written, the subject of the commit it names."""
    found, ambiguous = apply_mod.references(plan, changes, edits)
    subjects = {c.sha: c.subject for c in plan.history}
    if found:
        print("\nReferences to rewritten commits, updated on apply:")
        for ref in found:
            print(f"  {ref.commit.sha[:8]}  {ref.token}  ({subjects[ref.target]})")
    if ambiguous:
        print("\nLeft as is, matches more than one rewritten commit:")
        for ref in ambiguous:
            print(f"  {ref.commit.sha[:8]}  {ref.token}")


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
    author_info: bool,
    commit_info: bool,
    edit_info: bool,
    stat: bool,
    abbrev: bool,
    continue_: bool,
    force: bool,
    sha_rewrite: bool = True,
) -> bool:
    commits = git.get_commits(commit_range)
    if not commits:
        print("No commits found in the specified range")
        return False

    # A GitError here (range not in HEAD's history, unborn HEAD) propagates
    # to reword_command, which prints it and exits 1.
    plan = apply_mod.plan(commits)

    print(f"Found {len(commits)} commits to potentially reword")

    if len(commits) > 100:
        print(f"Warning: {len(commits)} commits is a lot. If this is unexpected, your")
        print("origin/HEAD symref may be stale — fix with: git remote set-head origin --auto")
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
            commits,
            repo_url=git.repo_url(),
            commit_link=commit_link,
            author_info=author_info,
            commit_info=commit_info,
            edit_info=edit_info,
            abbrev=abbrev,
            stats=git.get_stats(commit_range) if stat else None,
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

        edits = resolve(commits, result, edit_file)
        if edits is None:
            keep = True
            return False

        changes = apply_mod.changed_commits(commits, edits)
        if not changes:
            print("No changes detected")
            return True

        print(f"\nDetected {len(changes)} changed commit(s):")
        for commit, edit in changes:
            print(f"\ncommit {commit.sha[:8]}")
            for key, (old, new) in edit.info_changes(commit).items():
                print(f"  {key + ':':<12}{old} -> {new}")
            if edit.message != commit.message:
                print(message_diff(commit.message, edit.message, color=sys.stdout.isatty()))

        if sha_rewrite:
            print_references(plan, changes, edits)

        if not confirm("\nApply these changes?", default=True):
            print("Cancelled")
            keep = True
            return False

        print(f"\nPre-reword HEAD: {plan.head}")
        print(f"To revert:       git reset --soft {plan.head}")

        try:
            success = apply_mod.apply(plan, changes, edits, rewrite_shas=sha_rewrite)
        except GitError as e:
            print(f"Error: {e}")
            success = False
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
    author_info: Annotated[
        bool,
        typer.Option("--author-info", help="Add Author and AuthorDate info lines per commit"),
    ] = False,
    commit_info: Annotated[
        bool,
        typer.Option(
            "--commit-info", help="Add Commit and CommitDate info lines per commit (the committer)"
        ),
    ] = False,
    edit_info: Annotated[
        bool,
        typer.Option(
            "--edit-info",
            help=(
                "Apply the info lines as written, edited or not; implies --author-info. "
                "Without it they are context and edits to them are ignored"
            ),
        ),
    ] = False,
    stat: Annotated[
        bool, typer.Option("--stat", help="Add the files each commit touched, as comments")
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
    sha_rewrite: Annotated[
        bool,
        typer.Option(
            "--sha-rewrite/--no-sha-rewrite",
            help="Update shas of rewritten commits named in messages",
        ),
    ] = True,
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
            author_info=author_info,
            commit_info=commit_info,
            edit_info=edit_info,
            stat=stat,
            abbrev=abbrev,
            continue_=continue_,
            force=force,
            sha_rewrite=sha_rewrite,
        )
    except GitError as e:
        print(f"Error: {e}")
        raise typer.Exit(1) from None

    sys.exit(0 if success else 1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
