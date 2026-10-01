"""Read and write the reword edit file.

Format: prose/spec/reword-format.md. Git-log style: structure at column 0,
message content indented by 4 spaces (or one tab).

`parse` never raises on bad input. Every problem becomes a `Diagnostic` with
a line number so the CLI and the language server report the same things.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from git_reword.git import Commit, Stat

SUBJECT_MAX = 72
WIDTH = 72  # body text columns, excluding the 4-space indent

HEADER = """\
# git-reword: edit the indented messages. Column-0 lines are structure.
# Do not edit, reorder, add or remove `commit` lines.

"""

# The `edit-info` option: every info line is applied to its commit as
# written; the directive line tells the reader (and the language server,
# which never sees the command line) that this file is in that mode.
EDIT_INFO_OPTION = "edit-info"
KNOWN_OPTIONS = frozenset({EDIT_INFO_OPTION})
EDIT_INFO_HEADER = f"""\
# git-reword: edit the indented messages and the `Key: value` info lines.
# Column-0 lines are structure. Do not edit, reorder, add or remove
# `commit` lines. Info lines are applied as written; a commit gets git's
# default for a line that is missing.
# git-reword-options: {EDIT_INFO_OPTION}

"""

# Info line keys, named as in `git log --pretty=fuller`. The author pair is
# editable and applied; the committer pair is display only, since a
# rewritten commit always gets the current user and time as committer.
AUTHOR_KEYS = ("Author", "AuthorDate")
COMMITTER_KEYS = ("Commit", "CommitDate")
INFO_KEYS = frozenset(AUTHOR_KEYS + COMMITTER_KEYS)
_INFO_WIDTH = 12  # `AuthorDate: ` is the widest key, as in --pretty=fuller

_COMMIT_RE = re.compile(r"^commit[ \t]+(?P<sha>\S+)[ \t]*$")
# Full sha or an abbreviation; git accepts 4 hex digits as the shortest.
_SHA_RE = re.compile(r"^[0-9a-f]{4,64}$")
_INFO_RE = re.compile(r"^(?P<key>[A-Za-z][A-Za-z-]*):(?P<value>.*)$")
# A comment to the grammar, a directive to the tool.
_OPTIONS_RE = re.compile(r"^#[ \t]*git-reword-options:(?P<options>.*)$")
# Message content that looks like a trailer; same shape as the grammar's trailer_key.
_TRAILER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*:[ \t]")


class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Diagnostic:
    line: int  # 0-based
    message: str
    code: str
    severity: Severity = Severity.ERROR
    col: int = 0
    end_col: int | None = None  # None: to end of line


@dataclass
class Block:
    sha: str
    line: int  # 0-based line of the `commit` line
    end_line: int = 0  # exclusive; the block spans lines [line, end_line)
    message: str = ""  # cleaned message, "" when empty
    subject_line: int | None = None
    info: dict[str, str] = field(default_factory=dict)  # key -> stripped value
    info_lines: dict[str, int] = field(default_factory=dict)  # key -> 0-based line
    # [message_start, message_end): from the first to the last line that is
    # message content or a malformed line after the `commit` line. Leaves
    # out the comment, info and blank lines before it and the comment and
    # blank lines after it (the writer's margin and `--stat` block).
    message_start: int | None = None
    message_end: int | None = None

    @property
    def message_lines(self) -> tuple[int, int]:
        """[start, end) of the message lines; empty at the block end when
        there are none."""
        if self.message_start is None or self.message_end is None:
            return self.end_line, self.end_line
        return self.message_start, self.message_end


@dataclass
class ParseResult:
    blocks: list[Block]
    diagnostics: list[Diagnostic]
    options: set[str] = field(default_factory=set)  # from `# git-reword-options:` lines

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity is Severity.ERROR]

    @property
    def edit_info(self) -> bool:
        return EDIT_INFO_OPTION in self.options

    @property
    def messages(self) -> dict[str, str]:
        """sha -> cleaned message, for every block."""
        return {b.sha: b.message for b in self.blocks}


def cleanup(message: str) -> str:
    """Git's `--cleanup=whitespace`: trim trailing whitespace on each line,
    strip leading and trailing blank lines, collapse runs of blank lines.
    Comments are kept."""
    lines = [line.rstrip() for line in message.split("\n")]
    out: list[str] = []
    for line in lines:
        if not line and (not out or not out[-1]):
            continue
        out.append(line)
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)


def _strip_indent(line: str) -> str | None:
    """Content of a message line, or None when not indented."""
    if line.startswith("    "):
        return line[4:]
    if line.startswith("\t"):
        return line[1:]
    return None


def reflow(lines: list[str], width: int = WIDTH) -> list[str]:
    """Rewrap indented message lines to `width` columns of text, 4-space
    indented. Words are joined on single spaces; a word longer than `width`
    stays on its own line. Greedy on purpose: that is what people expect
    from a commit message, unlike textwrap's handling of long words."""
    words = " ".join(_strip_indent(line) or line for line in lines).split()
    out: list[str] = []
    current: list[str] = []
    length = 0
    for word in words:
        if current and length + 1 + len(word) > width:
            out.append("    " + " ".join(current))
            current, length = [], 0
        current.append(word)
        length += len(word) + (1 if length else 0)
    if current:
        out.append("    " + " ".join(current))
    return out


def parse(text: str) -> ParseResult:
    blocks: list[Block] = []
    diagnostics: list[Diagnostic] = []
    options: set[str] = set()
    seen: dict[str, int] = {}

    block: Block | None = None
    raw_message: list[str] = []  # content lines (indent stripped)

    def error(
        line: int, message: str, code: str, *, col: int = 0, end_col: int | None = None
    ) -> None:
        diagnostics.append(Diagnostic(line, message, code, col=col, end_col=end_col))

    def close_block(end_line: int) -> None:
        nonlocal block, raw_message
        if block is None:
            return
        block.end_line = end_line
        block.message = cleanup("\n".join(raw_message))
        if not block.message:
            error(
                block.line,
                f"Commit {block.sha[:8]} has an empty message; write a subject "
                "(empty messages cannot be applied, even for commits that had one)",
                "empty-message",
            )
        blocks.append(block)
        block = None
        raw_message = []

    def mark_message(lineno: int) -> None:
        if block is not None:
            if block.message_start is None:
                block.message_start = lineno
            block.message_end = lineno + 1

    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # trailing newline

    for lineno, line in enumerate(lines):
        if m := _OPTIONS_RE.match(line):
            if block is not None or blocks:
                error(lineno, "Options line after the first `commit` line", "misplaced-options")
                continue
            for option in m.group("options").split():
                if option in KNOWN_OPTIONS:
                    options.add(option)
                else:
                    error(lineno, f"Unknown option `{option}`", "unknown-option")
            continue
        if line.startswith("#"):
            continue

        if not line.strip():
            if block is not None and raw_message:
                raw_message.append("")
            continue

        if m := _COMMIT_RE.match(line):
            close_block(lineno)
            sha = m.group("sha")
            col = m.start("sha")
            if not _SHA_RE.match(sha):
                error(lineno, f"Not a sha: {sha}", "bad-sha", col=col, end_col=len(line))
            elif sha in seen:
                error(
                    lineno,
                    f"Duplicate commit {sha[:8]}, first seen on line {seen[sha] + 1}",
                    "duplicate-sha",
                    col=col,
                    end_col=len(line),
                )
            seen.setdefault(sha, lineno)
            block = Block(sha=sha, line=lineno)
            continue

        content = _strip_indent(line)
        if content is not None:
            if block is None:
                error(lineno, "Message line before any `commit` line", "orphan-line")
                continue
            mark_message(lineno)
            if not raw_message:
                block.subject_line = lineno
                _check_subject(content, lineno, len(line) - len(content), diagnostics)
            elif len(raw_message) == 1 and content.strip():
                diagnostics.append(
                    Diagnostic(
                        lineno,
                        "Second line of a message should be blank",
                        "second-line-not-blank",
                        Severity.WARNING,
                    )
                )
            raw_message.append(content)
            continue

        if (m := _INFO_RE.match(line)) and block is not None and not raw_message:
            block.info[m.group("key")] = m.group("value").strip()
            block.info_lines[m.group("key")] = lineno
            continue

        # Anything else at column 0.
        mark_message(lineno)
        if block is not None and raw_message:
            hint = "message lines must be indented by 4 spaces"
        elif block is not None:
            hint = "expected an indented message line or `Key: value` info line"
        else:
            hint = "expected a `commit <sha>` line or a `#` comment"
        if line[0] == " ":
            code = "short-indent"
        elif block is not None and raw_message:
            code = "unindented-line"  # inside a message: quick-fix is to indent
        else:
            code = "unexpected-line"
        error(lineno, f"Unexpected line at column 0; {hint}", code)

    close_block(len(lines))
    return ParseResult(blocks, diagnostics, options)


def _check_subject(subject: str, lineno: int, indent: int, diagnostics: list[Diagnostic]) -> None:
    # Git strips trailing whitespace, so it does not count towards the length.
    length = len(subject.rstrip())
    if length > SUBJECT_MAX:
        diagnostics.append(
            Diagnostic(
                lineno,
                f"Subject is {length} characters, keep it under {SUBJECT_MAX}",
                "subject-too-long",
                Severity.WARNING,
                col=indent + SUBJECT_MAX,
            )
        )
    if subject.rstrip().endswith("."):
        diagnostics.append(
            Diagnostic(
                lineno,
                "Subject ends with a period",
                "subject-trailing-period",
                Severity.WARNING,
            )
        )


def info_line(key: str, value: str) -> str:
    """One info line, value aligned as `git log --pretty=fuller` does it."""
    return f"{key + ':':<{_INFO_WIDTH}}{value}\n"


def info_values(commit: Commit) -> dict[str, str]:
    """The four info keys and their values for a commit."""
    return {
        "Author": commit.author,
        "AuthorDate": commit.author_date,
        "Commit": commit.committer,
        "CommitDate": commit.committer_date,
    }


def write(
    commits: list[Commit],
    *,
    repo_url: str | None = None,
    commit_link: bool = False,
    author_info: bool = False,
    commit_info: bool = False,
    edit_info: bool = False,
    abbrev: bool = True,
    stats: dict[str, Stat | None] | None = None,
) -> str:
    """Render commits to the edit file. Blocks are separated by one blank
    line; within a block one blank line separates the header lines from the
    message, as in git log. With `abbrev` the commit line carries the short
    sha (when the commit has one); comments and URLs keep the full sha.
    `author_info` and `commit_info` add the author and committer info
    lines; `edit_info` marks them editable (the header and the options
    directive) and implies `author_info`. `stats` (from `git.get_stats`)
    adds a comment block after each message, as `git log --stat` does: the
    summary line, then one line per file. A commit absent from `stats` (a
    merge) gets none."""
    author_info = author_info or edit_info
    keys = (AUTHOR_KEYS if author_info else ()) + (COMMITTER_KEYS if commit_info else ())
    out = [EDIT_INFO_HEADER if edit_info else HEADER]
    for i, commit in enumerate(commits):
        if i:
            out.append("\n")
        out.append(f"commit {commit.short if abbrev and commit.short else commit.sha}\n")
        if commit_link and repo_url:
            out.append(f"# {repo_url}/-/commit/{commit.sha}\n")
        values = info_values(commit)
        for key in keys:
            if values[key]:
                out.append(info_line(key, values[key]))
        out.append("\n")
        for line in commit.message.split("\n"):
            out.append(f"    {line}\n" if line else "\n")
        if stats is not None and (stat := stats.get(commit.sha)) is not None:
            out.append(stat_block(stat))
    return "".join(out)


def stat_block(stat: Stat) -> str:
    """The `--stat` comment block, leading blank line included: the summary,
    then one line per file. Also what the language server inserts."""
    lines = [stat.summary or "no files changed"] + [f"{s}  {p}" for s, p in stat.files]
    return "\n" + "".join(f"#   {line}\n" for line in lines)
