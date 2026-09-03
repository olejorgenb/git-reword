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

from git_reword.git import Commit

SUBJECT_MAX = 72
WIDTH = 72  # body text columns, excluding the 4-space indent

HEADER = """\
# git-reword: edit the indented messages. Column-0 lines are structure.
# Do not edit, reorder, add or remove `commit` lines.

"""

_COMMIT_RE = re.compile(r"^commit[ \t]+(?P<sha>\S+)[ \t]*$")
_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_INFO_RE = re.compile(r"^(?P<key>[A-Za-z][A-Za-z-]*):(?P<value>.*)$")
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
    info: dict[str, str] = field(default_factory=dict)


@dataclass
class ParseResult:
    blocks: list[Block]
    diagnostics: list[Diagnostic]

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity is Severity.ERROR]

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
            error(block.line, f"Commit {block.sha[:8]} has an empty message", "empty-message")
        blocks.append(block)
        block = None
        raw_message = []

    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # trailing newline

    for lineno, line in enumerate(lines):
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
                error(lineno, f"Not a full sha: {sha}", "bad-sha", col=col, end_col=len(line))
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
            if not raw_message:
                block.subject_line = lineno
                _check_subject(content, lineno, diagnostics)
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
            continue

        # Anything else at column 0.
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
    return ParseResult(blocks, diagnostics)


def _check_subject(subject: str, lineno: int, diagnostics: list[Diagnostic]) -> None:
    if len(subject) > SUBJECT_MAX:
        diagnostics.append(
            Diagnostic(
                lineno,
                f"Subject is {len(subject)} characters, keep it under {SUBJECT_MAX}",
                "subject-too-long",
                Severity.WARNING,
                col=4 + SUBJECT_MAX,
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


def write(
    commits: list[Commit],
    *,
    repo_url: str | None = None,
    commit_link: bool = False,
    info: bool = False,
) -> str:
    """Render commits to the edit file. Blocks are separated by one blank
    line; within a block one blank line separates the header lines from the
    message, as in git log."""
    out = [HEADER]
    for i, commit in enumerate(commits):
        if i:
            out.append("\n")
        out.append(f"commit {commit.sha}\n")
        if commit_link and repo_url:
            out.append(f"# {repo_url}/-/commit/{commit.sha}\n")
        if info:
            if commit.author:
                out.append(f"Author: {commit.author}\n")
            if commit.date:
                out.append(f"Date:   {commit.date}\n")
        out.append("\n")
        for line in commit.message.split("\n"):
            out.append(f"    {line}\n" if line else "\n")
    return "".join(out)
