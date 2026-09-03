from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

import pytest
from lsprotocol import types as lsp

from git_reword import format as fmt
from git_reword import git
from git_reword.lsp.analysis import OPEN_COMMIT_COMMAND, Analysis, Repo
from git_reword.lsp.open import open_url, opener


@pytest.fixture
def edit_file(repo: Path) -> Path:
    commits = git.get_commits("HEAD~3..HEAD", cwd=repo)
    path = repo / "REWORD_EDITMSG"
    path.write_text(fmt.write(commits))
    return path


def analyse(path: Path, text: str | None = None) -> Analysis:
    return Analysis(
        path.as_uri(), text if text is not None else path.read_text(), Repo.discover(path)
    )


def apply_edits(text: str, edits: list[lsp.TextEdit]) -> str:
    lines = text.split("\n")

    def offset(p: lsp.Position) -> int:
        return sum(len(line) + 1 for line in lines[: p.line]) + p.character

    for e in sorted(edits, key=lambda e: offset(e.range.start), reverse=True):
        text = text[: offset(e.range.start)] + e.new_text + text[offset(e.range.end) :]
    return text


def test_repo_discovery(edit_file: Path, repo: Path):
    r = Repo.discover(edit_file)
    assert r is not None
    assert r.git_dir == repo / ".git"
    assert r.root == repo
    assert r.url == "https://gitlab.com/group/repo"
    assert Repo.discover(Path("/nonexistent/x.reword")) is None


def test_repo_discovery_from_inside_git_dir(repo: Path):
    """Edit files from older versions live in .git/; no root there."""
    old = repo / ".git" / "REWORD_EDITMSG"
    old.write_text("")
    r = Repo.discover(old)
    assert r is not None
    assert r.git_dir == repo / ".git"
    assert r.root is None


def test_commit_lookup_only_for_sha_shaped_tokens(repo: Path, tmp_path: Path):
    """Non-sha tokens never reach git: no revision resolving, no option injection."""
    r = Repo(git.git_dir(cwd=repo))
    assert r.commit("HEAD") is None  # a valid revision, but the parser rejects it

    out = tmp_path / "out"
    assert r.commit(f"--output={out}") is None
    assert not out.exists()

    sha = git.get_commit("HEAD", cwd=repo).sha
    commit = r.commit(sha[:8])
    assert commit is not None
    assert commit.sha == sha


def test_clean_file_has_no_diagnostics(edit_file: Path):
    assert analyse(edit_file).diagnostics() == []


def test_diagnostics_from_parser_and_from_git(edit_file: Path):
    text = edit_file.read_text()
    text = text.replace("    Third commit", "  Third commit")
    text = text.replace("    First commit", "    First commit, edited")
    lines = text.split("\n")
    sha_line = next(i for i, line in enumerate(lines) if line.startswith("commit "))
    lines[sha_line] = "commit " + "0" * 40
    text = "\n".join(lines)

    diags = analyse(edit_file, text).diagnostics()
    by_code = {d.code: d for d in diags}
    assert set(by_code) == {"short-indent", "unknown-sha", "changed"}
    assert by_code["short-indent"].severity is lsp.DiagnosticSeverity.Error
    assert by_code["unknown-sha"].range.start.character == 7
    assert by_code["changed"].severity is lsp.DiagnosticSeverity.Hint
    assert all(d.source == "git-reword" for d in diags)


def test_symbols(edit_file: Path):
    text = edit_file.read_text().replace("    Second commit", "    Second commit, edited")
    symbols = analyse(edit_file, text).symbols()
    assert [s.name for s in symbols] == ["First commit", "Second commit, edited", "Third commit"]
    assert symbols[1].detail.endswith(" · changed")
    assert symbols[0].detail == symbols[0].detail.split()[0]  # just the short sha
    assert symbols[0].range.start.line < symbols[1].range.start.line
    assert symbols[0].range.end.line < symbols[1].range.start.line


def test_hover_on_commit_line_only(edit_file: Path):
    a = analyse(edit_file)
    block = a.result.blocks[1]
    hover = a.hover(lsp.Position(block.line, 3))
    assert hover is not None
    value = hover.contents.value
    assert "Test <test@example.com>" in value
    assert "# a hash line that must survive" in value
    assert "changed" not in value
    assert a.hover(lsp.Position(block.line + 1, 3)) is None


def test_links(edit_file: Path):
    a = analyse(edit_file)
    links = a.links()
    assert len(links) == 3
    # The file is written with abbreviated shas; links carry the full one.
    assert len(a.result.blocks[0].sha) < 40
    assert len(a.full_sha(a.result.blocks[0])) == 40
    assert (
        links[0].target
        == f"https://gitlab.com/group/repo/-/commit/{a.full_sha(a.result.blocks[0])}"
    )
    assert links[0].tooltip == "Open in browser"
    assert links[0].range.start.character == 7

    # In Zed the sha links to Zed's commit view instead.
    z = Analysis(a.uri, a.text, a.repo, client="Zed Preview")
    assert [x.tooltip for x in z.links()] == ["Open in Zed"] * 3
    assert z.links()[0].target.startswith(
        f"zed://git/commit/{a.full_sha(a.result.blocks[0])}?repo="
    )


def test_folding_ranges(edit_file: Path):
    a = analyse(edit_file)
    ranges = a.folding_ranges()
    blocks = a.result.blocks
    # The two-line header is the only comment run in the fixture.
    comments = [r for r in ranges if r.kind == lsp.FoldingRangeKind.Comment]
    assert [(r.start_line, r.end_line, r.collapsed_text) for r in comments] == [(0, 1, None)]
    by_start = {r.start_line: r for r in ranges if r.kind == lsp.FoldingRangeKind.Region}
    for i, b in enumerate(blocks):
        assert b.subject_line is not None
        block_fold = by_start[b.line]
        assert block_fold.collapsed_text == "\u00b7 " + b.message.split("\n")[0]
        # Ends on the last non-blank line, so the separator stays visible.
        assert a.lines[block_fold.end_line].strip()
        assert block_fold.end_line < b.end_line
        if i + 1 < len(blocks):
            assert block_fold.end_line < blocks[i + 1].line
        if "\n" in b.message:
            body_fold = by_start[b.subject_line]
            assert body_fold.collapsed_text is None
            assert body_fold.end_line == block_fold.end_line
        else:
            assert b.subject_line not in by_start  # nothing to fold under the subject
    assert any("\n" in b.message for b in blocks)  # the fixture has a body to fold

    # No message: only the block fold, without placeholder.
    text = "commit " + blocks[0].sha + "\nAuthor: x\n\n"
    ranges = Analysis(a.uri, text, a.repo).folding_ranges()
    assert [(r.start_line, r.end_line, r.collapsed_text) for r in ranges] == [(0, 1, None)]


def test_comment_runs_fold_to_their_first_line(edit_file: Path):
    a = analyse(edit_file)
    sha = a.result.blocks[0].sha
    text = (
        "# one\n"  # a lone comment does not fold
        f"commit {sha}\n# 2 files changed\n# M  a\n# A  b\n\n    Subject\n# x\n# y\n    Body\n"
    )
    ranges = Analysis(a.uri, text, a.repo).folding_ranges()
    comments = [r for r in ranges if r.kind == lsp.FoldingRangeKind.Comment]
    assert [(r.start_line, r.end_line) for r in comments] == [(2, 4), (7, 8)]
    assert all(r.collapsed_text is None for r in comments)
    # The block fold still spans the whole block, comment runs included.
    block = next(r for r in ranges if r.start_line == 1)
    assert block.end_line == 9


def test_indent_quick_fix(edit_file: Path):
    text = edit_file.read_text().replace("    Body text", "Body text")
    a = analyse(edit_file, text)
    line = next(i for i, ln in enumerate(a.lines) if ln == "Body text")
    actions = a.code_actions(lsp.Range(lsp.Position(line, 0), lsp.Position(line, 0)))
    fix = next(x for x in actions if x.kind == lsp.CodeActionKind.QuickFix)
    assert fix.title == "Indent line"
    assert fix.diagnostics and fix.diagnostics[0].code == "unindented-line"
    fixed = apply_edits(text, fix.edit.changes[a.uri])
    assert fixed == edit_file.read_text()


def test_revert_and_open_actions(edit_file: Path):
    original = edit_file.read_text()
    text = original.replace("    Second commit", "    Second commit, edited")
    a = analyse(edit_file, text)
    block = a.result.blocks[1]
    actions = a.code_actions(
        lsp.Range(lsp.Position(block.line + 2, 0), lsp.Position(block.line + 2, 0))
    )
    titles = [x.title for x in actions]
    assert titles == [
        f"Revert {block.sha[:8]} to its original message",
        f"Open {block.sha[:8]} in browser",
    ]
    reverted = apply_edits(text, actions[0].edit.changes[a.uri])
    assert reverted == original
    assert actions[1].command.command == OPEN_COMMIT_COMMAND
    assert actions[1].command.arguments == [
        f"https://gitlab.com/group/repo/-/commit/{a.full_sha(block)}"
    ]

    # Unchanged block: only the open action.
    other = a.result.blocks[0]
    actions = a.code_actions(lsp.Range(lsp.Position(other.line, 0), lsp.Position(other.line, 0)))
    assert [x.title for x in actions] == [f"Open {other.sha[:8]} in browser"]


def test_open_in_zed_only_for_zed(edit_file: Path, repo: Path):
    a = analyse(edit_file)
    block = a.result.blocks[0]
    at = lsp.Range(lsp.Position(block.line, 0), lsp.Position(block.line, 0))
    assert [x.title for x in a.code_actions(at)] == [f"Open {block.sha[:8]} in browser"]

    # The name carries the release channel: "Zed Preview", "Zed Dev", ...
    a = Analysis(a.uri, a.text, a.repo, client="Zed Preview")
    actions = a.code_actions(at)
    assert [x.title for x in actions] == [
        f"Open {block.sha[:8]} in Zed",
        f"Open {block.sha[:8]} in browser",
    ]
    assert actions[0].command.arguments == [
        f"zed://git/commit/{a.full_sha(block)}?repo={quote(str(repo))}"
    ]

    # No worktree root (old edit file under .git/): no Zed action.
    old = repo / ".git" / "REWORD_EDITMSG"
    old.write_text(a.text)
    a = Analysis(old.as_uri(), a.text, Repo.discover(old), client="Zed")
    assert [x.title for x in a.code_actions(at)] == [f"Open {block.sha[:8]} in browser"]


def test_opener_choice():
    assert opener("zed://git/commit/abc?repo=%2Fx", platform="linux") == [
        "zed",
        "zed://git/commit/abc?repo=%2Fx",
    ]
    assert opener("https://x/y", platform="linux") == ["xdg-open", "https://x/y"]
    assert opener("https://x/y", platform="darwin") == ["open", "https://x/y"]


def test_open_url_reports_missing_opener(monkeypatch):
    monkeypatch.setattr("git_reword.lsp.open.opener", lambda url: ["no-such-opener-xyz", url])
    error = open_url("https://x/y")
    assert error is not None and "no-such-opener-xyz" in error


REFLOW_DOC = """\
commit 7dcfdad1afb39b697a8632f0c450c555abe7d5b6
    Subject line that is rather long and would wrap if it were treated as body text

    A body paragraph written
    with short lines.
# a column-0 comment inside the paragraph splits it
    More text after the
    comment.

        preformatted line
        another one

    Signed-off-by: Someone <s@example.com>
    Reviewed-by: Other <o@example.com>
"""


def _reflow_actions(a: Analysis, line: int) -> list[lsp.CodeAction]:
    at = lsp.Range(lsp.Position(line, 0), lsp.Position(line, 0))
    return [x for x in a.code_actions(at) if x.title == "Reflow paragraph"]


def test_reflow_action_on_body_paragraph_only(tmp_path: Path):
    path = tmp_path / "x.reword"
    a = Analysis(path.as_uri(), REFLOW_DOC, None)
    assert a.paragraph_at(1) is None  # subject
    assert a.paragraph_at(2) is None  # blank
    assert a.paragraph_at(3) == (3, 5)
    assert a.paragraph_at(5) is None  # comment
    assert a.paragraph_at(7) == (6, 8)
    assert a.paragraph_at(9) is None  # preformatted
    assert a.paragraph_at(12) is None  # trailers
    assert a.paragraph_at(0) is None  # commit line

    (action,) = _reflow_actions(a, 4)
    new = apply_edits(REFLOW_DOC, action.edit.changes[a.uri])
    assert new.split("\n")[3:5] == [
        "    A body paragraph written with short lines.",
        "# a column-0 comment inside the paragraph splits it",
    ]
    assert _reflow_actions(a, 1) == []
    assert _reflow_actions(a, 12) == []

    # Already wrapped: nothing to offer.
    b = Analysis(path.as_uri(), new, None)
    assert _reflow_actions(b, 3) == []


def test_reflow_body_that_looks_like_trailers_but_is_not_last(tmp_path: Path):
    doc = (
        "commit 7dcfdad1afb39b697a8632f0c450c555abe7d5b6\n    S\n\n"
        "    Note: one\n    Note: two\n\n    tail\n"
    )
    a = Analysis((tmp_path / "x.reword").as_uri(), doc, None)
    assert a.paragraph_at(3) == (3, 5)
    assert a.paragraph_at(6) == (6, 7)


def test_revert_last_block_keeps_file_shape(edit_file: Path):
    original = edit_file.read_text()
    text = original.replace("    Third commit", "    Third commit, edited")
    a = analyse(edit_file, text)
    block = a.result.blocks[-1]
    actions = a.code_actions(lsp.Range(lsp.Position(block.line, 0), lsp.Position(block.line, 0)))
    assert apply_edits(text, actions[0].edit.changes[a.uri]) == original


def test_revert_leaves_stat_block_alone(edit_file: Path, repo: Path):
    """A --stat block after the message is not part of it: revert stops
    before it, for a middle block and for the last one."""
    commits = git.get_commits("HEAD~3..HEAD", cwd=repo)
    original = fmt.write(commits, stats=git.get_stats("HEAD~3..HEAD", cwd=repo))
    assert original.count("# A  f") == 3
    for subject in ("Second commit", "Third commit"):
        text = original.replace(f"    {subject}", f"    {subject}, edited")
        a = analyse(edit_file, text)
        block = next(b for b in a.result.blocks if a.changed(b))
        actions = a.code_actions(
            lsp.Range(lsp.Position(block.line, 0), lsp.Position(block.line, 0))
        )
        assert apply_edits(text, actions[0].edit.changes[a.uri]) == original


def test_formatting(edit_file: Path):
    original = edit_file.read_text()
    text = original.replace("    Body text", "  Body text  ").replace(
        "    Third commit", "\tThird commit"
    )
    text += "\n\n"
    a = analyse(edit_file, text)
    edits = a.format_edits()
    assert len(edits) == 1
    assert apply_edits(text, edits) == original
    assert analyse(edit_file, original).format_edits() == []


def test_without_repo_still_parses(tmp_path: Path):
    path = tmp_path / "x.reword"
    text = f"commit {'a' * 40}\n    Subject\n  body\n"
    a = Analysis(path.as_uri(), text, Repo.discover(path))
    assert a.repo is None
    assert [d.code for d in a.diagnostics()] == ["short-indent"]
    assert a.links() == []
    assert a.hover(lsp.Position(0, 0)) is None


# -- stdio smoke test ---------------------------------------------------------


class Client:
    def __init__(self, proc: subprocess.Popen[bytes]) -> None:
        self.proc = proc
        self.next_id = 0

    def send(self, method: str, params: dict, *, request: bool = True) -> int | None:
        msg: dict = {"jsonrpc": "2.0", "method": method, "params": params}
        if request:
            self.next_id += 1
            msg["id"] = self.next_id
        body = json.dumps(msg).encode()
        assert self.proc.stdin is not None
        self.proc.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
        self.proc.stdin.flush()
        return msg.get("id")

    def read(self) -> dict:
        assert self.proc.stdout is not None
        length = 0
        while True:
            line = self.proc.stdout.readline()
            if line in (b"\r\n", b""):
                break
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":")[1])
        return json.loads(self.proc.stdout.read(length))

    def wait_for(self, *, method: str | None = None, id_: int | None = None) -> dict:
        for _ in range(50):
            msg = self.read()
            if method and msg.get("method") == method:
                return msg
            if id_ is not None and msg.get("id") == id_:
                return msg
        raise AssertionError(f"no message with method={method} id={id_}")


def test_stdio_server(edit_file: Path, repo: Path, tmp_path_factory):
    text = edit_file.read_text().replace("    Third commit", "  Third commit")
    # A fake `zed` CLI on PATH records what the server asks it to open.
    bin_dir = tmp_path_factory.mktemp("bin")
    opened = bin_dir / "opened"
    fake_zed = bin_dir / "zed"
    fake_zed.write_text(f'#!/bin/sh\necho "$1" > "{opened}"\n')
    fake_zed.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "git_reword.lsp.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        c = Client(proc)
        init_id = c.send(
            "initialize",
            {
                "processId": None,
                "rootUri": repo.as_uri(),
                "clientInfo": {"name": "Zed", "version": "0.0.0"},
                "capabilities": {},  # no window/showDocument, like Zed
            },
        )
        init = c.wait_for(id_=init_id)
        caps = init["result"]["capabilities"]
        assert caps["hoverProvider"] and caps["documentSymbolProvider"]
        assert caps["codeActionProvider"] and caps["documentLinkProvider"]
        assert caps["documentFormattingProvider"]
        assert OPEN_COMMIT_COMMAND in caps["executeCommandProvider"]["commands"]
        c.send("initialized", {}, request=False)

        uri = edit_file.as_uri()
        c.send(
            "textDocument/didOpen",
            {"textDocument": {"uri": uri, "languageId": "reword", "version": 1, "text": text}},
            request=False,
        )
        published = c.wait_for(method="textDocument/publishDiagnostics")
        codes = [d["code"] for d in published["params"]["diagnostics"]]
        assert codes == ["short-indent", "changed"]  # dedenting the subject changed the message

        sym_id = c.send("textDocument/documentSymbol", {"textDocument": {"uri": uri}})
        symbols = c.wait_for(id_=sym_id)["result"]
        assert [s["name"] for s in symbols] == ["First commit", "Second commit", "Body"]

        # Code action on the first commit line, then run its command: the
        # server has no showDocument to lean on, so it must call `zed`.
        line = {"line": 3, "character": 0}
        ca_id = c.send(
            "textDocument/codeAction",
            {
                "textDocument": {"uri": uri},
                "range": {"start": line, "end": line},
                "context": {"diagnostics": []},
            },
        )
        actions = c.wait_for(id_=ca_id)["result"]
        zed_action = next(x for x in actions if x["title"].endswith("in Zed"))
        cmd = zed_action["command"]
        ex_id = c.send(
            "workspace/executeCommand", {"command": cmd["command"], "arguments": cmd["arguments"]}
        )
        c.wait_for(id_=ex_id)
        for _ in range(50):
            if opened.exists():
                break
            time.sleep(0.05)
        assert opened.read_text().strip() == cmd["arguments"][0]
        assert opened.read_text().startswith("zed://git/commit/")

        shutdown_id = c.send("shutdown", {})
        c.wait_for(id_=shutdown_id)
        c.send("exit", {}, request=False)
        assert proc.wait(timeout=10) == 0
    finally:
        if proc.poll() is None:
            proc.kill()


def test_unknown_abbreviation(edit_file: Path):
    a = analyse(edit_file)
    text = a.text.replace(f"commit {a.result.blocks[0].sha}", "commit 0123456", 1)
    a = analyse(edit_file, text)
    assert [d.code for d in a.diagnostics()] == ["unknown-sha"]
    # No commit to resolve through: the link falls back to the token as written.
    assert a.links()[0].target.endswith("/-/commit/0123456")
