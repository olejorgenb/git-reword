"""Editor-independent analysis of one reword document.

Everything the language server answers is computed here from the document
text plus a `Repo` for git lookups. The pygls wiring in `server.py` is thin.
Results are lsprotocol types so the server can hand them straight back.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from urllib.parse import quote, urlencode

from lsprotocol import types as lsp
from pygls.uris import to_fs_path

from git_reword import format as fmt
from git_reword import git
from git_reword.apply import Edit, block_edit
from git_reword.git import Commit, GitError

SOURCE = "git-reword"
OPEN_COMMIT_COMMAND = "git-reword.openCommit"
ADD_STATS = "add-stats"  # `data["action"]` of the file stats actions, for resolve
INDENT_FIX_CODES = frozenset({"short-indent", "unindented-line"})
SUBJECT_SEP = "\u00b7 "  # before the subject in a folded block
AGENT_COMMITS_MAX = 100  # commits listed in an agent prompt; the agent can read the rest
# A `--stat` file line: `#   M  path`, or `#   R  old -> new` for renames and copies.
_STAT_LINE_RE = re.compile(r"^#\s+[MADTRC]  (?:.* -> )?(?P<path>.+)$")
# A `--stat` summary line: `#   2 files changed, ...` or `#   no files changed`.
_STAT_SUMMARY_RE = re.compile(r"^#\s+(?:\d+ files? changed|no files changed)")

# Shas in messages are coloured by the server (the grammar sees one `text`
# node per line): a constant variable, styled like the grammar's @constant.
SEMANTIC_LEGEND = lsp.SemanticTokensLegend(token_types=["variable"], token_modifiers=["constant"])

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
    _on_head: dict[str, bool] = field(default_factory=dict, repr=False)

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
        # The parser rejects such tokens anyway; also keeps option-shaped ones away from git.
        if not fmt.SHA_RE.match(sha):
            return None
        if sha not in self._commits:
            try:
                self._commits[sha] = git.get_commit(sha, cwd=self.git_dir)
            except GitError:
                self._commits[sha] = None
        return self._commits[sha]

    def on_head(self, sha: str) -> bool:
        """Whether the commit is in HEAD's history. Cached for the life of
        the server; a failure counts as yes, so it stays quiet."""
        if sha not in self._on_head:
            try:
                self._on_head[sha] = git.is_ancestor(sha, "HEAD", cwd=self.git_dir)
            except GitError:
                self._on_head[sha] = True
        return self._on_head[sha]

    def commit_url(self, sha: str) -> str | None:
        return git.commit_url(self.url, sha) if self.url else None

    def zed_url(self, sha: str) -> str | None:
        """zed://git/commit/<sha>?repo=<root>, which opens Zed's commit view."""
        if self.root is None:
            return None
        return f"zed://git/commit/{sha}?repo={quote(str(self.root))}"


def _range(line: int, start: int, end: int) -> lsp.Range:
    return lsp.Range(lsp.Position(line, start), lsp.Position(line, end))


def _indent(message: str) -> str:
    return "".join(f"    {line}\n" if line else "\n" for line in message.split("\n"))


def _open_action(title: str, url: str, what: str = "commit") -> lsp.CodeAction:
    return lsp.CodeAction(
        title=title,
        kind=lsp.CodeActionKind.Empty,
        command=lsp.Command(title=f"Open {what}", command=OPEN_COMMIT_COMMAND, arguments=[url]),
    )


def agent_url(prompt: str) -> str:
    """zed://agent?prompt=<text>: Zed's agent panel, prompt filled in but not sent."""
    return "zed://agent?" + urlencode({"prompt": prompt}, quote_via=quote)


@dataclass
class Analysis:
    uri: str
    text: str
    repo: Repo | None
    client: str | None = None  # editor name from initialize, e.g. "Zed"
    _edits: dict[int, tuple[Edit, list[fmt.Diagnostic]]] = field(default_factory=dict, repr=False)

    @cached_property
    def lines(self) -> list[str]:
        return self.text.split("\n")

    @cached_property
    def result(self) -> fmt.ParseResult:
        return fmt.parse(self.text)

    @cached_property
    def message_shas(self) -> list[tuple[int, re.Match[str], Commit]]:
        """(line, match, commit) for every token in a message line that
        names a commit. Comment lines (`#` at column 0) are not scanned."""
        if self.repo is None:
            return []
        out = []
        for block in self.result.blocks:
            start, end = block.message_lines
            for i in range(start, end):
                if self.lines[i].startswith("#"):
                    continue
                for m in fmt.SHA_REF_RE.finditer(self.lines[i]):
                    if (commit := self.repo.commit(m[0])) is not None:
                        out.append((i, m, commit))
        return out

    def reminted_blocks(self) -> set[str]:
        """Full shas of the blocks an apply re-mints: from the first changed
        block to the end of the file."""
        blocks = self.result.blocks
        first = next((i for i, b in enumerate(blocks) if self.changed(b)), len(blocks))
        return {self.full_sha(b) for b in blocks[first:]}

    # -- lookups -----------------------------------------------------------

    def block_at(self, line: int) -> fmt.Block | None:
        for block in self.result.blocks:
            if block.line <= line < block.end_line:
                return block
        return None

    def original(self, block: fmt.Block) -> Commit | None:
        return self.repo.commit(block.sha) if self.repo else None

    def known_blocks(self) -> list[fmt.Block]:
        """Blocks whose sha names a commit."""
        return [b for b in self.result.blocks if self.original(b) is not None]

    def edit(self, block: fmt.Block) -> tuple[Edit, list[fmt.Diagnostic]] | None:
        """The block's edit and its info-line diagnostics, None without an
        original commit. Cached per block: edited author lines cost a git call."""
        if block.line not in self._edits:
            original = self.original(block)
            if original is None:
                return None
            cwd = self.repo.git_dir if self.repo else None
            self._edits[block.line] = block_edit(
                block, original, edit_info=self.result.edit_info, cwd=cwd
            )
        return self._edits[block.line]

    def changed(self, block: fmt.Block) -> bool:
        # An empty message is an error, not a change, as in changed_commits.
        return bool(block.message) and bool(self.changed_parts(block))

    def changed_parts(self, block: fmt.Block) -> list[str]:
        """Which of "message", "author" and "committer" the block changes."""
        original = self.original(block)
        edit = self.edit(block)
        if original is None or edit is None:
            return []
        parts = []
        if block.message and block.message != original.message:
            parts.append("message")
        changed_keys = edit[0].info_changes(original)
        if any(key in fmt.AUTHOR_KEYS for key in changed_keys):
            parts.append("author")
        if any(key in fmt.COMMITTER_KEYS for key in changed_keys):
            parts.append("committer")
        return parts

    def sha_range(self, block: fmt.Block) -> lsp.Range:
        line = self.lines[block.line]
        start = line.index(block.sha)
        return _range(block.line, start, start + len(block.sha))

    def line_range(self, line: int) -> lsp.Range:
        return _range(line, 0, len(self.lines[line]))

    def has_stat(self, block: fmt.Block) -> bool:
        """Whether a comment line after the message is a stat summary."""
        _, end = block.message_lines
        return any(_STAT_SUMMARY_RE.match(self.lines[i]) for i in range(end, block.end_line))

    def wants_stat(self, block: fmt.Block) -> bool:
        """Whether the file stats actions apply: a known non-merge commit
        with a message and no stat block. Uses only the cached commit
        lookup, no stat lookup, since editors ask on every cursor move."""
        original = self.original(block)
        return (
            original is not None
            and len(original.parents) <= 1
            and block.subject_line is not None
            and not self.has_stat(block)
        )

    def stat_edits(self, shas: list[str]) -> list[lsp.TextEdit]:
        """Insert a stat block after the message of each block whose full sha
        is in `shas` and that still wants one. One stat lookup for all."""
        wanted = set(shas)
        blocks = [
            b for b in self.result.blocks if self.full_sha(b) in wanted and self.wants_stat(b)
        ]
        if not blocks or self.repo is None:
            return []
        stats = git.get_commit_stats([self.full_sha(b) for b in blocks], cwd=self.repo.git_dir)
        edits = []
        for b in blocks:
            if (stat := stats.get(self.full_sha(b))) is not None:
                at = lsp.Position(b.message_lines[1], 0)
                edits.append(lsp.TextEdit(lsp.Range(at, at), fmt.stat_block(stat)))
        return edits

    def stat_action(self, title: str, shas: list[str], *, lazy: bool) -> lsp.CodeAction:
        """A file stats action. With `lazy` the edit is left to
        codeAction/resolve, which calls `stat_edits` with `data`."""
        return lsp.CodeAction(
            title=title,
            kind=lsp.CodeActionKind.RefactorRewrite,
            data={"action": ADD_STATS, "uri": self.uri, "shas": shas},
            edit=None if lazy else lsp.WorkspaceEdit(changes={self.uri: self.stat_edits(shas)}),
        )

    def all_stats_action(self, blocks: list[fmt.Block], *, lazy: bool) -> lsp.CodeAction:
        return self.stat_action(
            "Add file stats to all commits", [self.full_sha(b) for b in blocks], lazy=lazy
        )

    def agent_prompt(self, focus: fmt.Block | None = None) -> str | None:
        """The prompt for Zed's agent: about `focus`, or about every known
        commit as a series. None without a worktree root or known commits."""
        if self.repo is None or self.repo.root is None:
            return None
        listed = [focus] if focus is not None else self.known_blocks()
        if not listed or any(self.original(b) is None for b in listed):
            return None
        root = self.repo.root
        path = Path(to_fs_path(self.uri) or self.uri)
        if path.is_relative_to(root):
            path = path.relative_to(root)

        if focus is not None:
            task = (
                f"Help me improve the commit message of {self.full_sha(focus)} "
                f"(the block at line {focus.line + 1}) in `{path}`."
            )
        else:
            task = (
                f"Help me improve the commit messages in `{path}`, as a series: "
                "consistent wording, and each message explaining its own commit."
            )
        commits = [f"{b.sha} {b.message.partition('\n')[0]}" for b in listed[:AGENT_COMMITS_MAX]]
        if len(listed) > AGENT_COMMITS_MAX:
            commits.append(f"... and {len(listed) - AGENT_COMMITS_MAX} more, see the file")
        heading = "Commit:" if focus is not None else "Commits, in file order:"
        editable = "message lines and info lines" if self.result.edit_info else "message lines"
        sections = [
            task,
            f"`{path}` is a git-reword edit file in the repository at `{root}`. "
            "The user applies it with `git reword` when we are done, which rewords "
            "the commits.",
            "\n".join([heading, *commits]),
            "Run `git show --stat --patch <sha>` to see a commit's change, and "
            "`git log` to see how this repository writes messages.",
            "Let's discuss first. Once we agree, edit the file directly.",
            fmt.AGENT_GUIDE.rstrip("\n"),
            "Limits:\n"
            f"- Change only {editable}.\n"
            "- Leave `commit` lines as they are. Commits cannot be split, squashed, "
            "reordered, added or dropped here.\n"
            "- Do not run `git reword`, commit, or change the repository otherwise.",
            "If you can see diagnostics for the file, fix the ones in lines you changed.",
        ]
        return "\n\n".join(sections)

    def paragraph_at(self, line: int) -> tuple[int, int] | None:
        """[start, end) of the body paragraph containing `line`: a run of
        non-blank message lines. None on the subject, in a trailer block, in
        a preformatted paragraph (extra indent), or off any message line."""
        block = self.block_at(line)
        if block is None or line == block.subject_line:
            return None
        msg_start, msg_end = block.message_lines

        def content(i: int) -> str | None:
            c = fmt.strip_indent(self.lines[i])
            return c if c is not None and c.strip() else None

        if not (msg_start <= line < msg_end) or content(line) is None:
            return None
        start = line
        while start > msg_start and content(start - 1) is not None:
            start -= 1
        end = line + 1
        while end < msg_end and content(end) is not None:
            end += 1
        if start == block.subject_line:
            return None
        texts = [content(i) or "" for i in range(start, end)]
        if any(t.startswith((" ", "\t")) for t in texts):
            return None
        is_last = all(content(i) is None for i in range(end, msg_end))
        if is_last and all(fmt.TRAILER_RE.match(t) for t in texts):
            return None
        return start, end

    # -- features ----------------------------------------------------------

    def parse_diagnostics(self) -> list[lsp.Diagnostic]:
        """The parser's diagnostics, no git lookups."""
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
        return out

    def diagnostics(self) -> list[lsp.Diagnostic]:
        out = self.parse_diagnostics()
        if self.repo is None:
            return out
        for block in self.result.blocks:
            if not fmt.SHA_RE.match(block.sha):
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
                continue
            edit = self.edit(block)
            for d in edit[1] if edit else []:
                out.append(
                    lsp.Diagnostic(
                        range=self.line_range(d.line),
                        message=d.message,
                        severity=_SEVERITY[d.severity],
                        code=d.code,
                        source=SOURCE,
                    )
                )
            if self.changed(block):
                out.append(
                    lsp.Diagnostic(
                        range=self.line_range(block.line),
                        message=f"{' and '.join(self.changed_parts(block)).capitalize()} changed",
                        severity=lsp.DiagnosticSeverity.Hint,
                        code="changed",
                        source=SOURCE,
                    )
                )
        reminted = self.reminted_blocks()
        for line, m, commit in self.message_shas:
            if commit.sha in reminted:
                code = "sha-rewritten"
                message = "Updated to the new sha on apply (unless --no-sha-rewrite)"
            elif not self.repo.on_head(commit.sha):
                code, message = "sha-not-on-branch", "Not on this branch; rewritten or dropped?"
            else:
                continue
            out.append(
                lsp.Diagnostic(
                    range=_range(line, m.start(), m.end()),
                    message=message,
                    severity=lsp.DiagnosticSeverity.Hint,
                    code=code,
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
        for line, m, commit in self.message_shas:
            if line == position.line and m.start() <= position.character < m.end():
                value = (
                    f"`{commit.short or commit.sha[:8]}` {commit.subject}  \n"
                    f"Author: {commit.author} · {commit.author_date}"
                )
                return lsp.Hover(
                    contents=lsp.MarkupContent(lsp.MarkupKind.Markdown, value),
                    range=_range(line, m.start(), m.end()),
                )
        block = self.block_at(position.line)
        if block is None or position.line != block.line:
            return None
        original = self.original(block)
        if original is None:
            return None
        parts = [
            f"`{original.sha[:8]}`  \n"
            f"Author: {original.author} · {original.author_date}  \n"
            f"Commit: {original.committer} · {original.committer_date}"
        ]
        if changed := self.changed_parts(block):
            parts.append(f"**{' and '.join(changed).capitalize()} changed.** Original message:")
        else:
            parts.append("Original message:")
        parts.append(f"```\n{original.message}\n```")
        return lsp.Hover(
            contents=lsp.MarkupContent(lsp.MarkupKind.Markdown, "\n\n".join(parts)),
            range=self.sha_range(block),
        )

    def semantic_tokens(self) -> lsp.SemanticTokens:
        """One token per sha in a message that names a commit, encoded
        relative to the previous one as LSP wants."""
        data: list[int] = []
        prev_line = prev_start = 0
        for line, m, _ in self.message_shas:
            start = m.start() - prev_start if line == prev_line else m.start()
            data += [line - prev_line, start, m.end() - m.start(), 0, 1]
            prev_line, prev_start = line, m.start()
        return lsp.SemanticTokens(data=data)

    @property
    def is_zed(self) -> bool:
        # clientInfo.name is "Zed", "Zed Preview", "Zed Nightly" or "Zed Dev".
        return self.client is not None and self.client.startswith("Zed")

    def full_sha(self, block: fmt.Block) -> str:
        """The commit's full sha when git knows it, else the token as written
        (which may be an abbreviation)."""
        original = self.original(block)
        return original.sha if original is not None else block.sha

    def link_target(self, sha: str) -> tuple[str, str] | None:
        """(url, tooltip) for a sha: Zed's commit view in Zed, else the forge."""
        if self.repo is None:
            return None
        if self.is_zed and (url := self.repo.zed_url(sha)):
            return url, "Open in Zed"
        if url := self.repo.commit_url(sha):
            return url, "Open in browser"
        return None

    def links(self) -> list[lsp.DocumentLink]:
        out = []
        for b in self.result.blocks:
            if fmt.SHA_RE.match(b.sha) and (target := self.link_target(self.full_sha(b))):
                url, tooltip = target
                out.append(lsp.DocumentLink(range=self.sha_range(b), target=url, tooltip=tooltip))
        for line, m, commit in self.message_shas:
            if target := self.link_target(commit.sha):
                url, tooltip = target
                link_range = _range(line, m.start(), m.end())
                out.append(lsp.DocumentLink(range=link_range, target=url, tooltip=tooltip))
        root = self.repo.root if self.repo else None
        if root is None:
            return out
        for i, line in enumerate(self.lines):
            if (m := _STAT_LINE_RE.match(line)) and (root / m["path"]).is_file():
                out.append(
                    lsp.DocumentLink(
                        range=_range(i, m.start("path"), m.end("path")),
                        target=(root / m["path"]).as_uri(),
                        tooltip="Open file",
                    )
                )
        return out

    def folding_ranges(self) -> list[lsp.FoldingRange]:
        """Two folds per commit: the block from the `commit` line, showing the
        subject as collapsed text, and the body from the subject line. Either
        way the subject stays visible; indent folding would hide it. Plus one
        per run of two or more comment lines, folding to its first line: a
        `--stat` block collapses to its summary, the header to its first
        line."""
        out = []
        start: int | None = None
        for i, line in enumerate([*self.lines, ""]):
            if line.startswith("#"):
                start = i if start is None else start
                continue
            if start is not None and i - start > 1:
                out.append(
                    lsp.FoldingRange(
                        start_line=start, end_line=i - 1, kind=lsp.FoldingRangeKind.Comment
                    )
                )
            start = None
        for block in self.result.blocks:
            last = block.end_line - 1
            while last > block.line and not self.lines[last].strip():
                last -= 1
            if last <= block.line:
                continue
            subject = block.subject_line
            # Zed trims the placeholder text, so a plain leading space would
            # vanish and the chip would touch the sha; a dot separator stays.
            collapsed = (
                f"{SUBJECT_SEP}{block.message.split(chr(10), 1)[0]}"
                if subject is not None
                else None
            )
            out.append(
                lsp.FoldingRange(
                    start_line=block.line,
                    end_line=last,
                    kind=lsp.FoldingRangeKind.Region,
                    collapsed_text=collapsed or None,
                )
            )
            if subject is not None and last > subject:
                out.append(
                    lsp.FoldingRange(
                        start_line=subject, end_line=last, kind=lsp.FoldingRangeKind.Region
                    )
                )
        return out

    def code_actions(self, range_: lsp.Range, *, lazy: bool = False) -> list[lsp.CodeAction]:
        """Actions for the range. `lazy` when the client resolves `edit`
        through codeAction/resolve: expensive edits are then left out."""
        line = range_.start.line
        block = self.block_at(line)
        return [
            *self._indent_actions(range_),
            *self._revert_actions(block),
            *self._reflow_actions(line),
            *self._wrap_actions(),
            *self._stat_actions(block, lazy=lazy),
            *self._open_actions(block),
            *self._agent_actions(block),
        ]

    def _indent_actions(self, range_: lsp.Range) -> list[lsp.CodeAction]:
        # Only the parser reports these, so skip the git-backed diagnostics.
        fixable = [
            d
            for d in self.parse_diagnostics()
            if d.code in INDENT_FIX_CODES
            and range_.start.line <= d.range.start.line <= range_.end.line
        ]
        if not fixable:
            return []
        edits = [
            lsp.TextEdit(
                self.line_range(d.range.start.line),
                "    " + self.lines[d.range.start.line].lstrip(" "),
            )
            for d in fixable
        ]
        return [
            lsp.CodeAction(
                title="Indent line" if len(edits) == 1 else f"Indent {len(edits)} lines",
                kind=lsp.CodeActionKind.QuickFix,
                diagnostics=fixable,
                edit=lsp.WorkspaceEdit(changes={self.uri: edits}),
                is_preferred=True,
            )
        ]

    def _revert_actions(self, block: fmt.Block | None) -> list[lsp.CodeAction]:
        if block is None or not self.changed(block):
            return []
        original = self.original(block)
        assert original is not None
        edits: list[lsp.TextEdit] = []
        originals = fmt.info_values(original)
        for key, line in block.info_lines.items():
            if key in fmt.INFO_KEYS and block.info[key] != originals[key]:
                edits.append(
                    lsp.TextEdit(
                        lsp.Range(lsp.Position(line, 0), lsp.Position(line + 1, 0)),
                        fmt.info_line(key, originals[key]),
                    )
                )
        if block.message != original.message:
            start, end = block.message_lines
            edits.append(
                lsp.TextEdit(
                    lsp.Range(lsp.Position(start, 0), lsp.Position(end, 0)),
                    _indent(original.message),
                )
            )
        what = " and ".join(self.changed_parts(block))
        return [
            lsp.CodeAction(
                title=f"Revert {block.sha[:8]} to its original {what}",
                kind=lsp.CodeActionKind.RefactorRewrite,
                edit=lsp.WorkspaceEdit(changes={self.uri: edits}),
            )
        ]

    def _reflow_actions(self, line: int) -> list[lsp.CodeAction]:
        paragraph = self.paragraph_at(line)
        if paragraph is None:
            return []
        start, end = paragraph
        new_lines = fmt.reflow(self.lines[start:end])
        if new_lines == self.lines[start:end]:
            return []
        edit = lsp.TextEdit(
            lsp.Range(lsp.Position(start, 0), lsp.Position(end, 0)),
            "".join(f"{line}\n" for line in new_lines),
        )
        return [
            lsp.CodeAction(
                title="Reflow paragraph",
                kind=lsp.CodeActionKind.RefactorRewrite,
                edit=lsp.WorkspaceEdit(changes={self.uri: [edit]}),
            )
        ]

    def wrap_edits(self) -> list[lsp.TextEdit]:
        """Break every body line longer than WIDTH that can be broken, each
        piece behind the line's own indent. Subjects are left alone."""
        edits = []
        for block in self.result.blocks:
            if block.subject_line is None:
                continue
            for i in range(block.subject_line + 1, block.message_lines[1]):
                line = self.lines[i]
                text = fmt.strip_indent(line)
                if text is None or len(text) <= fmt.WIDTH:
                    continue
                pieces = fmt.wrap_line(text)
                if len(pieces) == 1:
                    continue
                indent = line[: len(line) - len(text)]
                edits.append(
                    lsp.TextEdit(self.line_range(i), "\n".join(indent + p for p in pieces))
                )
        return edits

    def _wrap_actions(self) -> list[lsp.CodeAction]:
        edits = self.wrap_edits()
        if not edits:
            return []
        return [
            lsp.CodeAction(
                title="Wrap long lines in all commits",
                kind=lsp.CodeActionKind.RefactorRewrite,
                edit=lsp.WorkspaceEdit(changes={self.uri: edits}),
            )
        ]

    def _stat_actions(self, block: fmt.Block | None, *, lazy: bool) -> list[lsp.CodeAction]:
        """One for the block, one for all commits when more than one wants it."""
        stat_blocks = [b for b in self.result.blocks if self.wants_stat(b)] if self.repo else []
        actions = []
        # wants_stat is what put a block in stat_blocks, so ask it directly.
        if block is not None and self.wants_stat(block):
            actions.append(
                self.stat_action(
                    f"Add file stats to {block.sha[:8]}", [self.full_sha(block)], lazy=lazy
                )
            )
        if len(stat_blocks) > 1:
            actions.append(self.all_stats_action(stat_blocks, lazy=lazy))
        return actions

    def _open_actions(self, block: fmt.Block | None) -> list[lsp.CodeAction]:
        if block is None or self.repo is None:
            return []
        actions = []
        sha = self.full_sha(block)
        if self.is_zed and (zed_url := self.repo.zed_url(sha)):
            actions.append(_open_action(f"Open {block.sha[:8]} in Zed", zed_url))
        if url := self.repo.commit_url(sha):
            actions.append(_open_action(f"Open {block.sha[:8]} in browser", url))
        return actions

    def _agent_actions(self, block: fmt.Block | None) -> list[lsp.CodeAction]:
        if not self.is_zed:
            return []
        actions = []
        if block is not None and (prompt := self.agent_prompt(block)):
            title = f"Discuss {block.sha[:8]} with agent"
            actions.append(_open_action(title, agent_url(prompt), "agent"))
        if len(self.known_blocks()) > 1 and (prompt := self.agent_prompt()):
            title = "Discuss all messages with agent"
            actions.append(_open_action(title, agent_url(prompt), "agent"))
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
