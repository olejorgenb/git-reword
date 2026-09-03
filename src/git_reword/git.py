"""Git plumbing helpers."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitError(Exception):
    pass


@dataclass
class Commit:
    """A git commit with its full (cleaned) message."""

    sha: str
    message: str
    author: str = ""  # "Name <email>"
    date: str = ""  # author date, iso

    @property
    def subject(self) -> str:
        return self.message.split("\n", 1)[0]


def run(*args: str, cwd: Path | str | None = None) -> str:
    """Run a git command and return stripped stdout."""
    try:
        result = subprocess.run(["git", *args], capture_output=True, text=True, check=True, cwd=cwd)
    except subprocess.CalledProcessError as e:
        raise GitError(e.stderr.strip() or str(e)) from e
    return result.stdout.strip()


def in_repo() -> bool:
    try:
        run("rev-parse", "--git-dir")
    except GitError:
        return False
    return True


def git_dir(cwd: Path | str | None = None) -> Path:
    return Path(run("rev-parse", "--absolute-git-dir", cwd=cwd))


def repo_url(cwd: Path | str | None = None) -> str | None:
    """Derive the forge project URL from the origin remote."""
    try:
        url = run("remote", "get-url", "origin", cwd=cwd)
    except GitError:
        return None
    # SSH: git@gitlab.com:group/repo.git
    if url.startswith("git@"):
        url = url[4:]  # gitlab.com:group/repo.git
        url = url.replace(":", "/", 1)  # gitlab.com/group/repo.git
        url = "https://" + url
    return url.removesuffix(".git")


def get_commit(sha: str, cwd: Path | str | None = None) -> Commit:
    from git_reword.format import cleanup

    out = subprocess.run(
        ["git", "log", "-n", "1", "--date=iso", "--format=%an <%ae>%n%ad%n%B", sha],
        capture_output=True,
        text=True,
        check=True,
        cwd=cwd,
    ).stdout
    author, date, message = out.split("\n", 2)
    return Commit(sha=sha, message=cleanup(message), author=author, date=date)


def get_commits(commit_range: str) -> list[Commit]:
    """All commits in the range, oldest first."""
    shas = [s for s in run("rev-list", "--reverse", commit_range).split("\n") if s]
    return [get_commit(sha) for sha in shas]


def detect_branch_range() -> str:
    """Commit range for the current feature branch using origin/HEAD."""
    try:
        main_ref = run("symbolic-ref", "refs/remotes/origin/HEAD")
    except GitError as e:
        raise GitError(
            "Could not determine main branch. Run: git remote set-head origin --auto"
        ) from e
    main_branch = main_ref.removeprefix("refs/remotes/origin/")
    return f"{main_branch}..HEAD"
