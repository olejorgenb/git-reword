"""Git plumbing helpers."""

from __future__ import annotations

import re
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


def config(key: str, cwd: Path | str | None = None) -> str | None:
    """`git config --get <key>`, or None when it is unset."""
    result = subprocess.run(
        ["git", "config", "--get", key], capture_output=True, text=True, cwd=cwd
    )
    if result.returncode == 1:
        return None
    if result.returncode != 0:
        raise GitError(result.stderr.strip() or f"git config exited {result.returncode}")
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


# scheme://[user@]host[:port]/path
_URL_REMOTE_RE = re.compile(
    r"^(?:ssh|git|https?)://(?:[^@/]+@)?(?P<host>[^:/]+)(?::\d+)?/(?P<path>.+)$"
)
# scp-like [user@]host:path
_SCP_REMOTE_RE = re.compile(r"^(?:[^@/]+@)?(?P<host>[^:/]+):(?P<path>.+)$")


def forge_url(remote: str) -> str | None:
    """https://host/path for a remote URL, None for anything that is not a
    forge (local paths, file://)."""
    pattern = _URL_REMOTE_RE if "://" in remote else _SCP_REMOTE_RE
    m = pattern.match(remote)
    if m is None:
        return None
    path = m["path"].removesuffix("/").removesuffix(".git")
    return f"https://{m['host']}/{path}"


def repo_url(cwd: Path | str | None = None) -> str | None:
    """Derive the forge project URL from the origin remote."""
    try:
        url = run("remote", "get-url", "origin", cwd=cwd)
    except GitError:
        return None
    return forge_url(url)


def commit_url(repo_url: str, sha: str) -> str:
    """Forge URL for a commit. GitHub style for github.com, GitLab style otherwise."""
    if "github.com" in repo_url:
        return f"{repo_url}/commit/{sha}"
    return f"{repo_url}/-/commit/{sha}"


# %H gives the full sha even for an abbreviated argument. %h is git's own
# abbreviation, unique in the repository and sized by core.abbrev. Records end
# with a NUL so a %B with blank lines in it stays one record.
_LOG_FORMAT = "--format=%H%n%h%n%an <%ae>%n%ad%n%B%x00"


def _parse_commit(record: str) -> Commit:
    from git_reword.format import cleanup

    full, short, author, date, *rest = record.strip("\n").split("\n", 4)
    message = rest[0] if rest else ""
    return Commit(sha=full, message=cleanup(message), author=author, date=date, short=short)


def get_commit(sha: str, cwd: Path | str | None = None) -> Commit:
    # --end-of-options so an option-shaped `sha` cannot turn into a git option.
    out = run("log", "-n", "1", "--date=iso", _LOG_FORMAT, "--end-of-options", sha, cwd=cwd)
    return _parse_commit(out.split("\0", 1)[0])


def get_commits(commit_range: str, cwd: Path | str | None = None) -> list[Commit]:
    """All commits in the range, oldest first."""
    out = run(
        "log", "--reverse", "--date=iso", _LOG_FORMAT, "--end-of-options", commit_range, cwd=cwd
    )
    return [_parse_commit(record) for record in out.split("\0") if record.strip()]


@dataclass
class Stat:
    """What a commit touched, for the `--stat` comment block."""

    summary: str  # git's --shortstat line, "" for an empty commit
    files: list[tuple[str, str]]  # (status letter, path or "old -> new")


# One record per commit, NUL first so a multi-line diff summary stays in it.
_STAT_FORMAT = "--format=%x00%H %P"


def _stat_records(commit_range: str, flag: str, cwd: Path | str | None) -> dict[str, list[str]]:
    """{sha: non-blank lines git printed after the header} for every commit
    in the range, skipping merges (their diff depends on which parent you
    ask about, so they get no stat)."""
    out = run("log", _STAT_FORMAT, flag, "--end-of-options", commit_range, cwd=cwd)
    records: dict[str, list[str]] = {}
    for record in out.split("\0"):
        if not record.strip():
            continue
        header, *body = record.split("\n")
        sha, *parents = header.split()
        if len(parents) > 1:
            continue
        records[sha] = [line for line in body if line.strip()]
    return records


def get_stats(commit_range: str, cwd: Path | str | None = None) -> dict[str, Stat | None]:
    """{sha: Stat} for the range; None for merge commits.

    Two log calls, because --name-status silences --shortstat when both
    are given.
    """
    names = _stat_records(commit_range, "--name-status", cwd)
    summaries = _stat_records(commit_range, "--shortstat", cwd)
    stats: dict[str, Stat | None] = {}
    for sha, lines in names.items():
        files = []
        for line in lines:
            status, *paths = line.split("\t")
            files.append((status[0], " -> ".join(paths)))
        summary = summaries.get(sha, [])
        stats[sha] = Stat(summary=summary[0].strip() if summary else "", files=files)
    return stats


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
