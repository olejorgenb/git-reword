"""Git plumbing helpers."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path


class GitError(Exception):
    pass


@dataclass
class Commit:
    """A git commit with its full (cleaned) message."""

    sha: str  # always full
    message: str
    author: str = ""  # "Name <email>"
    author_date: str = ""  # iso
    committer: str = ""  # "Name <email>"
    committer_date: str = ""  # iso
    short: str = ""  # git's %h abbreviation, "" when not looked up
    tree: str = ""
    parents: list[str] = field(default_factory=list)  # full shas, first parent first

    @property
    def subject(self) -> str:
        return self.message.split("\n", 1)[0]


def run(
    *args: str,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    input: str | None = None,
) -> str:
    """Run a git command and return stripped stdout. `env` is added to the
    inherited environment."""
    full_env = {**os.environ, **env} if env else None
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            check=True,
            cwd=cwd,
            env=full_env,
            input=input,
        )
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
# with a NUL so a %B with blank lines in it stays one record. The iso dates
# round-trip through GIT_AUTHOR_DATE to the second, offset included.
_LOG_FORMAT = "--format=%H%n%h%n%T%n%P%n%an <%ae>%n%ad%n%cn <%ce>%n%cd%n%B%x00"


def _parse_commit(record: str) -> Commit:
    from git_reword.format import cleanup

    full, short, tree, parents, author, author_date, committer, committer_date, *rest = (
        record.strip("\n").split("\n", 8)
    )
    message = rest[0] if rest else ""
    return Commit(
        sha=full,
        message=cleanup(message),
        author=author,
        author_date=author_date,
        committer=committer,
        committer_date=committer_date,
        short=short,
        tree=tree,
        parents=parents.split(),
    )


def _log(options: list[str], revs: list[str], cwd: Path | str | None) -> list[Commit]:
    # --end-of-options so an option-shaped rev cannot turn into a git option.
    out = run(
        "log", "--reverse", "--date=iso", _LOG_FORMAT, *options, "--end-of-options", *revs, cwd=cwd
    )
    return [_parse_commit(record) for record in out.split("\0") if record.strip()]


def get_commit(sha: str, cwd: Path | str | None = None) -> Commit:
    return _log(["-n", "1"], [sha], cwd)[0]


def get_commits(commit_range: str, cwd: Path | str | None = None) -> list[Commit]:
    """All commits in the range, oldest first."""
    return _log([], [commit_range], cwd)


def history(tip: str, exclude: list[str], cwd: Path | str | None = None) -> list[Commit]:
    """Commits reachable from `tip` but not from any of `exclude`, parents
    before children (`--topo-order`, reversed), so a walk can map each
    commit's parents before it reaches the commit."""
    return _log(["--topo-order"], [tip, *(f"^{sha}" for sha in exclude)], cwd)


def split_ident(ident: str) -> tuple[str, str]:
    """`Name <email>` -> (name, email). Raises GitError when the value has no
    `<email>` part; the rest is left to git."""
    name, sep, email = ident.rpartition(" <")
    if not sep or not email.endswith(">"):
        raise GitError(f"not a `Name <email>` identity: {ident!r}")
    return name, email[:-1]


def iso_date(raw: str) -> str:
    """Git's `--date=iso` rendering of a raw `<timestamp> <+-HHMM>` date."""
    timestamp, offset = raw.split()
    sign = -1 if offset[0] == "-" else 1
    delta = timedelta(hours=int(offset[1:3]), minutes=int(offset[3:5])) * sign
    return datetime.fromtimestamp(int(timestamp), timezone(delta)).strftime("%Y-%m-%d %H:%M:%S %z")


def _ident_env(role: str, ident: str, date: str | None) -> dict[str, str]:
    """GIT_AUTHOR_* or GIT_COMMITTER_* for an ident and optional date."""
    name, email = split_ident(ident)
    prefix = f"GIT_{role.upper()}"
    env = {f"{prefix}_NAME": name, f"{prefix}_EMAIL": email}
    if date is not None:
        env[f"{prefix}_DATE"] = date
    return env


def ident(
    role: str, value: str, date: str | None, cwd: Path | str | None = None
) -> tuple[str, str]:
    """Validate and normalise an identity through `git var GIT_AUTHOR_IDENT`
    or `GIT_COMMITTER_IDENT` (`role` is "author" or "committer"): returns
    (`Name <email>`, iso date) as git would store them. `date` is anything
    the `GIT_*_DATE` variables accept; None means now. Raises GitError
    with git's own message on a malformed date or an empty name (`invalid
    date format: ...`, `empty ident name ...`)."""
    env = _ident_env(role, value, date)
    try:
        out = run("var", f"GIT_{role.upper()}_IDENT", cwd=cwd, env=env)
    except GitError as e:
        raise GitError(str(e).removeprefix("fatal: ")) from None
    name_email, _, raw = out.rpartition("> ")
    return name_email + ">", iso_date(raw)


def commit_tree(
    tree: str,
    parents: list[str],
    message: str,
    *,
    author: str,
    author_date: str,
    committer: str | None = None,
    committer_date: str | None = None,
    cwd: Path | str | None = None,
) -> str:
    """Write a commit object and return its sha. Author is taken from the
    arguments; so is the committer when given, otherwise git's default
    applies (the configured user, now). Nothing else is touched. The
    message is stored as given plus a final newline: `commit-tree` does
    no cleanup of its own."""
    env = _ident_env("author", author, author_date)
    if committer is not None:
        env.update(_ident_env("committer", committer, committer_date))
    args = [arg for parent in parents for arg in ("-p", parent)]
    return run("commit-tree", tree, *args, cwd=cwd, env=env, input=message + "\n")


def update_ref(
    ref: str, new: str, old: str, *, message: str, cwd: Path | str | None = None
) -> None:
    """Point `ref` at `new`, provided it still points at `old`. Follows a
    symbolic ref (so `HEAD` moves the checked out branch) and writes
    `message` to the reflog."""
    run("update-ref", "-m", message, ref, new, old, cwd=cwd)


@dataclass
class Stat:
    """What a commit touched, for the `--stat` comment block."""

    summary: str  # git's --shortstat line, "" for an empty commit
    files: list[tuple[str, str]]  # (status letter, path or "old -> new")


# One record per commit, NUL first so a multi-line diff summary stays in it.
_STAT_FORMAT = "--format=%x00%H %P"


def _stat_records(
    revs: list[str], flag: str, cwd: Path | str | None, *, walk: bool = True
) -> dict[str, list[str]]:
    """{sha: non-blank lines git printed after the header} for every commit
    `revs` name (a range, or with `walk=False` exactly the given commits),
    skipping merges (their diff depends on which parent you ask about, so
    they get no stat)."""
    no_walk = [] if walk else ["--no-walk=unsorted"]
    out = run("log", _STAT_FORMAT, flag, *no_walk, "--end-of-options", *revs, cwd=cwd)
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


def _stats(revs: list[str], cwd: Path | str | None, *, walk: bool) -> dict[str, Stat | None]:
    # Two log calls, because --name-status silences --shortstat when both are given.
    names = _stat_records(revs, "--name-status", cwd, walk=walk)
    summaries = _stat_records(revs, "--shortstat", cwd, walk=walk)
    stats: dict[str, Stat | None] = {}
    for sha, lines in names.items():
        files = []
        for line in lines:
            status, *paths = line.split("\t")
            files.append((status[0], " -> ".join(paths)))
        summary = summaries.get(sha, [])
        stats[sha] = Stat(summary=summary[0].strip() if summary else "", files=files)
    return stats


def get_stats(commit_range: str, cwd: Path | str | None = None) -> dict[str, Stat | None]:
    """{sha: Stat} for the range; merge commits are left out."""
    return _stats([commit_range], cwd, walk=True)


def get_commit_stats(shas: list[str], cwd: Path | str | None = None) -> dict[str, Stat | None]:
    """{full sha: Stat} for exactly the given commits; merges are left out."""
    return _stats(shas, cwd, walk=False) if shas else {}


def detect_branch_range() -> str:
    """Commit range for the current feature branch using origin/HEAD."""
    try:
        main_ref = run("symbolic-ref", "refs/remotes/origin/HEAD")
    except GitError as e:
        raise GitError(
            "Could not determine main branch. Run: git remote set-head origin --auto"
        ) from e
    main_branch = main_ref.removeprefix("refs/remotes/")
    return f"{main_branch}..HEAD"
