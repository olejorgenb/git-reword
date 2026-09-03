"""Git plumbing helpers."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


class GitError(Exception):
    pass


@dataclass
class Commit:
    """A git commit with its full message."""

    sha: str
    subject: str
    body: str

    @property
    def full_message(self) -> str:
        if self.body:
            return f"{self.subject}\n\n{self.body}"
        return self.subject


def run(*args: str, check: bool = True) -> str:
    """Run a git command and return stripped stdout."""
    try:
        result = subprocess.run(["git", *args], capture_output=True, text=True, check=check)
    except subprocess.CalledProcessError as e:
        raise GitError(e.stderr.strip() or str(e)) from e
    return result.stdout.strip()


def in_repo() -> bool:
    try:
        run("rev-parse", "--git-dir")
    except GitError:
        return False
    return True


def repo_url() -> str | None:
    """Derive the forge project URL from the origin remote."""
    try:
        url = run("remote", "get-url", "origin")
    except GitError:
        return None
    # SSH: git@gitlab.com:group/repo.git
    if url.startswith("git@"):
        url = url[4:]  # gitlab.com:group/repo.git
        url = url.replace(":", "/", 1)  # gitlab.com/group/repo.git
        url = "https://" + url
    return url.removesuffix(".git")


def get_commits(commit_range: str) -> list[Commit]:
    """All commits in the range, oldest first, with full messages."""
    shas = [s for s in run("rev-list", "--reverse", commit_range).split("\n") if s]

    commits = []
    for sha in shas:
        full_msg = subprocess.run(
            ["git", "log", "--format=%B", "-n", "1", sha],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.rstrip("\n")
        lines = full_msg.split("\n")

        subject = lines[0] if lines else ""
        body_lines = lines[1:]
        while body_lines and not body_lines[0].strip():
            body_lines.pop(0)

        commits.append(Commit(sha=sha, subject=subject, body="\n".join(body_lines)))

    return commits


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
