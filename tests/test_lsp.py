from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from lsprotocol import types as lsp

from git_reword import format as fmt
from git_reword import git
from git_reword.lsp.analysis import OPEN_COMMIT_COMMAND, Analysis, Repo


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
    assert links[0].target == f"https://gitlab.com/group/repo/-/commit/{a.result.blocks[0].sha}"
    assert links[0].range.start.character == 7


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
    assert actions[1].command.arguments == [f"https://gitlab.com/group/repo/-/commit/{block.sha}"]

    # Unchanged block: only the open action.
    other = a.result.blocks[0]
    actions = a.code_actions(lsp.Range(lsp.Position(other.line, 0), lsp.Position(other.line, 0)))
    assert [x.title for x in actions] == [f"Open {other.sha[:8]} in browser"]


def test_revert_last_block_keeps_file_shape(edit_file: Path):
    original = edit_file.read_text()
    text = original.replace("    Third commit", "    Third commit, edited")
    a = analyse(edit_file, text)
    block = a.result.blocks[-1]
    actions = a.code_actions(lsp.Range(lsp.Position(block.line, 0), lsp.Position(block.line, 0)))
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


def test_stdio_server(edit_file: Path, repo: Path):
    text = edit_file.read_text().replace("    Third commit", "  Third commit")
    proc = subprocess.Popen(
        [sys.executable, "-m", "git_reword.lsp.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        c = Client(proc)
        init_id = c.send(
            "initialize", {"processId": None, "rootUri": repo.as_uri(), "capabilities": {}}
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

        shutdown_id = c.send("shutdown", {})
        c.wait_for(id_=shutdown_id)
        c.send("exit", {}, request=False)
        assert proc.wait(timeout=10) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
