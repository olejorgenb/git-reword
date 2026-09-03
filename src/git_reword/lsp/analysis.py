"""Editor-independent analysis of one reword document.

Everything the language server answers is computed here from the document
text plus a `Repo` for git lookups. The pygls wiring in `server.py` is thin.
Results are lsprotocol types so the server can hand them straight back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from lsprotocol import types as lsp

from git_reword import format as fmt
from git_reword import git
from git_reword.git import Commit, GitError

SOURCE = "git-reword"
OPEN_COMMIT_COMMAND = "git-reword.openCommit"
INDENT_FIX_CODES = frozenset({"short-indent", "unindented-line"})

_SEVERITY = {
    fmt.Severity.ERROR: lsp.DiagnosticSeverity.Error,
    fmt.Severity.WARNING: lsp.DiagnosticSeverity.Warning,
}


@dataclass
class Repo:
    """Git access for a document, with a cache of original commits."""

    git_dir: Path
    url: str | None = None
    root: Path | None = None  # worktree root; None inside a bare repo or .git/
    _commits: dict[str, Commit | None] = field(default_factory=dict, repr=False)

    @classmethod
    def discover(cls, path: Path) -> Repo | None:
        """Repo for the directory containing `path`, or None when not in one.

        Works from a worktree (the REWORD_EDITMSG case) and from inside
        `.git/`, where files from older versions of the tool may still live.
        """
        directory = path.parent
        if not directory.is_dir():
            return None
        try:
            git_dir = git.git_dir(cwd=directory)
        except GitError:
            return None
        return cls(git_dir, git.repo_url(cwd=directory), git.toplevel(cwd=directory))

    def commit(self, sha: str) -> Commit | None:
        if sha not in self._commits:
            try:
                self._commits[sha] = git.get_commit(sha, cwd=self.git_dir)
            except GitError:
                self._commits[sha] = None
        return self._commits[sha]

    def commit_url(self, sha: str) -> str | None:
        return git.commit_url(self.url, sha) if self.url else None


def _range(line: int, start: int, end: int) -> lsp.Range:
    return lsp.Range(lsp.Position(line, start), lsp.Position(line, end))


def _indent(message: str) -> str:
    return "".join(f"    {line}\n" if line else "\n" for line in message.split("\n"))


@dataclass
class Analysis:
    uri: str
    text: str
    repo: Repo | None

    @cached_property
    def lines(self) -> list[str]:
        return self.text.split("\n")

    @cached_property
    def result(self) -> fmt.ParseResult:
        return fmt.parse(self.text)

    # -- lookups -----------------------------------------------------------

    def block_at(self, line: int) -> fmt.Block | None:
        for block in self.result.blocks:
            if block.line <= line < block.end_line:
                return block
        return None

    def original(self, block: fmt.Block) -> Commit | None:
        return self.repo.commit(block.sha) if self.repo else None

    def changed(self, block: fmt.Block) -> bool:
        original = self.original(block)
        return original is not None and bool(block.message) and block.message != original.message

    def sha_range(self, block: fmt.Block) -> lsp.Range:
        line = self.lines[block.line]
        start = line.index(block.sha)
        return _range(block.line, start, start + len(block.sha))

    def line_range(self, line: int) -> lsp.Range:
        return _range(line, 0, len(self.lines[line]))

    def message_lines(self, block: fmt.Block) -> tuple[int, int]:
        """[start, end) of the lines holding the message: everything after the
        comment/info lines that directly follow the `commit` line."""
        start = block.line + 1
        while start < block.end_line and (
            self.lines[start].startswith("#") or fmt._INFO_RE.match(self.lines[start])
        ):
            start += 1
        return start, block.end_line

    # -- features ----------------------------------------------------------

    def diagnostics(self) -> list[lsp.Diagnostic]:
        out = []
        for d in self.result.diagnostics:
            end = d.end_col if d.end_col is not None else len(self.lines[d.line])
            out.append(
                lsp.Diagnostic(
                    range=_range(d.line, d.col, end),
                    message=d.message,
                    severity=_SEVERITY[d.severity],
                    code=d.code,
                    source=SOURCE,
                )
            )
        if self.repo is None:
            return out
        for block in self.result.blocks:
            if not fmt._SHA_RE.match(block.sha):
                continue  # already reported as bad-sha
            if self.original(block) is None:
                out.append(
                    lsp.Diagnostic(
                        range=self.sha_range(block),
                        message=f"Unknown commit {block.sha[:8]}",
                        severity=lsp.DiagnosticSeverity.Error,
                        code="unknown-sha",
                        source=SOURCE,
                    )
                )
            elif self.changed(block):
                out.append(
                    lsp.Diagnostic(
                        range=self.line_range(block.line),
                        message="Message changed",
                        severity=lsp.DiagnosticSeverity.Hint,
                        code="changed",
                        source=SOURCE,
                    )
                )
        return out

    def symbols(self) -> list[lsp.DocumentSymbol]:
        out = []
        for block in self.result.blocks:
            subject = block.message.split("\n", 1)[0] if block.message else "(empty message)"
            detail = block.sha[:8] + (" · changed" if self.changed(block) else "")
            end_line = max(block.line, block.end_line - 1)
            out.append(
                lsp.DocumentSymbol(
                    name=subject,
                    detail=detail,
                    kind=lsp.SymbolKind.Object,
                    range=lsp.Range(
                        lsp.Position(block.line, 0),
                        lsp.Position(end_line, len(self.lines[end_line])),
                    ),
                    selection_range=self.line_range(block.line),
                )
            )
        return out

    def hover(self, position: lsp.Position) -> lsp.Hover | None:
        block = self.block_at(position.line)
        if block is None or position.line != block.line:
            return None
        original = self.original(block)
        if original is None:
            return None
        parts = [f"`{original.sha[:8]}` {original.author} · {original.date}"]
        if self.changed(block):
            parts.append("**Message changed.** Original:")
        else:
            parts.append("Original message:")
        parts.append(f"```\n{original.message}\n```")
        return lsp.Hover(
            contents=lsp.MarkupContent(lsp.MarkupKind.Markdown, "\n\n".join(parts)),
            range=self.sha_range(block),
        )

    def links(self) -> list[lsp.DocumentLink]:
        if self.repo is None or self.repo.url is None:
            return []
        return [
            lsp.DocumentLink(range=self.sha_range(b), target=self.repo.commit_url(b.sha))
            for b in self.result.blocks
            if fmt._SHA_RE.match(b.sha)
        ]

    def code_actions(self, range_: lsp.Range) -> list[lsp.CodeAction]:
        actions: list[lsp.CodeAction] = []

        fixable = [
            d
            for d in self.diagnostics()
            if d.code in INDENT_FIX_CODES
            and range_.start.line <= d.range.start.line <= range_.end.line
        ]
        if fixable:
            edits = [
                lsp.TextEdit(
                    self.line_range(d.range.start.line),
                    "    " + self.lines[d.range.start.line].lstrip(" "),
                )
                for d in fixable
            ]
            actions.append(
                lsp.CodeAction(
                    title="Indent line" if len(edits) == 1 else f"Indent {len(edits)} lines",
                    kind=lsp.CodeActionKind.QuickFix,
                    diagnostics=fixable,
                    edit=lsp.WorkspaceEdit(changes={self.uri: edits}),
                    is_preferred=True,
                )
            )

        block = self.block_at(range_.start.line)
        if block is None:
            return actions

        if self.changed(block):
            original = self.original(block)
            assert original is not None
            start, end = self.message_lines(block)
            is_last = block is self.result.blocks[-1]
            new_text = _indent(original.message) + ("" if is_last else "\n")
            actions.append(
                lsp.CodeAction(
                    title=f"Revert {block.sha[:8]} to its original message",
                    kind=lsp.CodeActionKind.RefactorRewrite,
                    edit=lsp.WorkspaceEdit(
                        changes={
                            self.uri: [
                                lsp.TextEdit(
                                    lsp.Range(lsp.Position(start, 0), lsp.Position(end, 0)),
                                    new_text,
                                )
                            ]
                        }
                    ),
                )
            )

        url = self.repo.commit_url(block.sha) if self.repo else None
        if url:
            actions.append(
                lsp.CodeAction(
                    title=f"Open {block.sha[:8]} in browser",
                    kind=lsp.CodeActionKind.Empty,
                    command=lsp.Command(
                        title="Open commit", command=OPEN_COMMIT_COMMAND, arguments=[url]
                    ),
                )
            )
        return actions

    def formatted(self) -> str:
        """Normalised text: fixable indents fixed, tab indents to spaces,
        trailing whitespace trimmed, final newline."""
        fix_lines = {d.line for d in self.result.diagnostics if d.code in INDENT_FIX_CODES}
        out = []
        for i, line in enumerate(self.lines):
            if i in fix_lines:
                line = "    " + line.lstrip(" ")
            elif line.startswith("\t"):
                line = "    " + line[1:]
            out.append(line.rstrip())
        text = "\n".join(out)
        while text.endswith("\n\n"):
            text = text[:-1]
        if not text.endswith("\n"):
            text += "\n"
        return text

    def format_edits(self) -> list[lsp.TextEdit]:
        new_text = self.formatted()
        if new_text == self.text:
            return []
        last = len(self.lines) - 1
        whole = lsp.Range(lsp.Position(0, 0), lsp.Position(last, len(self.lines[last])))
        return [lsp.TextEdit(whole, new_text)]
