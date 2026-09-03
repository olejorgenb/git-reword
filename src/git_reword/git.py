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

    sha: str  # always full
    message: str
    author: str = ""  # "Name <email>"
    date: str = ""  # author date, iso
    short: str = ""  # git's %h abbreviation, "" when not looked up

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


def toplevel(cwd: Path | str | None = None) -> Path | None:
    """Root of the working tree, or None without one (bare repo, inside .git)."""
    try:
        return Path(run("rev-parse", "--show-toplevel", cwd=cwd))
    except GitError:
        return None


def common_dir(cwd: Path | str | None = None) -> Path:
    """The git dir shared by all worktrees of the repository."""
    return Path(run("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=cwd))


def is_ignored(path: Path | str, cwd: Path | str | None = None) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--", str(path)], capture_output=True, text=True, cwd=cwd
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise GitError(result.stderr.strip() or f"git check-ignore exited {result.returncode}")


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


def commit_url(repo_url: str, sha: str) -> str:
    """Forge URL for a commit. GitHub style for github.com, GitLab style otherwise."""
    if "github.com" in repo_url:
        return f"{repo_url}/commit/{sha}"
    return f"{repo_url}/-/commit/{sha}"


def get_commit(sha: str, cwd: Path | str | None = None) -> Commit:
    from git_reword.format import cleanup

    # `sha` may be an abbreviation; %H gives the full one back. %h is git's
    # own abbreviation, unique in the repository and sized by core.abbrev.
    out = run("log", "-n", "1", "--date=iso", "--format=%H%n%h%n%an <%ae>%n%ad%n%B", sha, cwd=cwd)
    full, short, author, date, message = out.split("\n", 4)
    return Commit(sha=full, message=cleanup(message), author=author, date=date, short=short)


def get_commits(commit_range: str, cwd: Path | str | None = None) -> list[Commit]:
    """All commits in the range, oldest first."""
    shas = [s for s in run("rev-list", "--reverse", commit_range, cwd=cwd).split("\n") if s]
    return [get_commit(sha, cwd=cwd) for sha in shas]


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
