from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, quote

import pytest
from lsprotocol import types as lsp

from git_reword import format as fmt
from git_reword import git
from git_reword.lsp.analysis import OPEN_COMMIT_COMMAND, Analysis, Repo
from git_reword.lsp.open import open_url, opener
from tests.conftest import git as git_cmd


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


def core_actions(actions: list[lsp.CodeAction]) -> list[lsp.CodeAction]:
    """Leave out the file stats actions, which every block of a file
    written without --stat gets, and the agent actions, which every known
    commit gets in Zed."""
    return [x for x in actions if not x.title.startswith(("Add file stats", "Discuss"))]


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


def _with_sha_lines(edit_file: Path, repo: Path) -> tuple[str, str]:
    """The edit file with Third's body naming Second by its 7-digit and
    full sha, plus a hex word and a comment line naming it; and Second's sha."""
    second = git_cmd("rev-parse", "HEAD~1", cwd=repo)
    text = edit_file.read_text().replace(
        "    Body\n",
        f"    Body\n# {second}\n    See {second[:7]} and {second}, defaced.\n",
    )
    return text, second


def test_shas_in_messages_link_and_hover(edit_file: Path, repo: Path):
    text, second = _with_sha_lines(edit_file, repo)
    a = analyse(edit_file, text)
    line = a.lines.index(f"    See {second[:7]} and {second}, defaced.")
    links = [x for x in a.links() if x.range.start.line == line]
    assert [(x.range.start.character, x.range.end.character) for x in links] == [
        (8, 15),
        (20, 60),
    ]
    assert {x.target for x in links} == {f"https://gitlab.com/group/repo/-/commit/{second}"}
    assert not [x for x in a.links() if x.range.start.line == line - 1], "comment line"

    for character in (8, 14, 20, 59):
        hover = a.hover(lsp.Position(line, character))
        assert hover is not None
        assert "Second commit" in hover.contents.value
        assert "Author: Test <test@example.com>" in hover.contents.value
    assert a.hover(lsp.Position(line, 63)) is None  # defaced
    assert a.hover(lsp.Position(line - 1, 4)) is None  # the comment line

    z = Analysis(a.uri, a.text, a.repo, client="Zed")
    targets = [x.target for x in z.links() if x.range.start.line == line]
    assert all(t.startswith(f"zed://git/commit/{second}?repo=") for t in targets)
    assert len(targets) == 2


def test_shas_in_messages_semantic_tokens(edit_file: Path, repo: Path):
    text, second = _with_sha_lines(edit_file, repo)
    a = analyse(edit_file, text)
    line = a.lines.index(f"    See {second[:7]} and {second}, defaced.")
    assert a.semantic_tokens().data == [line, 8, 7, 0, 1, 0, 12, 40, 0, 1]
    assert analyse(edit_file).semantic_tokens().data == []


def _sha_hints(a: Analysis) -> list[tuple[str | int | None, str]]:
    codes = {"sha-rewritten", "sha-not-on-branch"}
    return [
        (d.code, a.lines[d.range.start.line][d.range.start.character : d.range.end.character])
        for d in a.diagnostics()
        if d.code in codes
    ]


def test_sha_rewritten_hint(edit_file: Path, repo: Path):
    text, second = _with_sha_lines(edit_file, repo)
    assert _sha_hints(analyse(edit_file, text)) == []

    edited = text.replace("    First commit", "    First commit, edited")
    a = analyse(edit_file, edited)
    assert _sha_hints(a) == [("sha-rewritten", second[:7]), ("sha-rewritten", second)]
    hint = next(d for d in a.diagnostics() if d.code == "sha-rewritten")
    assert hint.severity is lsp.DiagnosticSeverity.Hint
    assert "--no-sha-rewrite" in hint.message


def test_sha_not_on_branch_hint(edit_file: Path, repo: Path):
    git_cmd("checkout", "-q", "-b", "side", cwd=repo)
    git_cmd("commit", "-q", "--allow-empty", "-m", "Side", cwd=repo)
    side = git_cmd("rev-parse", "HEAD", cwd=repo)
    git_cmd("checkout", "-q", "main", cwd=repo)
    base = git_cmd("rev-parse", "HEAD~3", cwd=repo)

    text = edit_file.read_text().replace(
        "    Body\n", f"    Body\n    Was {side[:7]}, builds on {base[:7]}.\n"
    )
    a = analyse(edit_file, text)
    assert _sha_hints(a) == [("sha-not-on-branch", side[:7])]


def test_stat_paths_link_to_files(edit_file: Path, repo: Path):
    commits = git.get_commits("HEAD~3..HEAD", cwd=repo)
    text = fmt.write(commits, stats=git.get_stats("HEAD~3..HEAD", cwd=repo))
    text = text.replace("#   A  f3\n", "#   A  f3\n#   R  f0 -> f2\n#   D  gone\n")
    a = analyse(edit_file, text)
    paths = [x for x in a.links() if x.tooltip == "Open file"]
    # f1, f2, f3 from the stats, f2 again as the rename target; `gone` does not exist.
    assert [x.target for x in paths] == [(repo / f).as_uri() for f in ("f1", "f2", "f3", "f2")]
    rename = paths[-1]
    line = a.lines[rename.range.start.line]
    assert line[rename.range.start.character : rename.range.end.character] == "f2"
    assert line.startswith("#   R  f0 -> ")


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
    actions = core_actions(
        a.code_actions(lsp.Range(lsp.Position(block.line + 2, 0), lsp.Position(block.line + 2, 0)))
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
    actions = core_actions(
        a.code_actions(lsp.Range(lsp.Position(other.line, 0), lsp.Position(other.line, 0)))
    )
    assert [x.title for x in actions] == [f"Open {other.sha[:8]} in browser"]


def test_open_in_zed_only_for_zed(edit_file: Path, repo: Path):
    a = analyse(edit_file)
    block = a.result.blocks[0]
    at = lsp.Range(lsp.Position(block.line, 0), lsp.Position(block.line, 0))
    assert [x.title for x in core_actions(a.code_actions(at))] == [
        f"Open {block.sha[:8]} in browser"
    ]

    # The name carries the release channel: "Zed Preview", "Zed Dev", ...
    a = Analysis(a.uri, a.text, a.repo, client="Zed Preview")
    actions = core_actions(a.code_actions(at))
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
    assert [x.title for x in core_actions(a.code_actions(at))] == [
        f"Open {block.sha[:8]} in browser"
    ]


def agent_actions(a: Analysis, line: int) -> dict[str, str]:
    """title -> prompt, for the agent actions at `line`."""
    actions = a.code_actions(lsp.Range(lsp.Position(line, 0), lsp.Position(line, 0)))
    prompts = {}
    for x in actions:
        if x.title.startswith("Discuss"):
            assert x.command.command == OPEN_COMMIT_COMMAND
            [url] = x.command.arguments
            scheme, query = url.split("?", 1)
            assert scheme == "zed://agent"
            assert list(parse_qs(query)) == ["prompt"]
            prompts[x.title] = parse_qs(query)["prompt"][0]
    return prompts


def test_agent_actions(edit_file: Path, repo: Path):
    a = analyse(edit_file)
    block = a.result.blocks[1]
    assert agent_actions(a, block.line) == {}, "only offered in Zed"

    a = Analysis(a.uri, a.text, a.repo, client="Zed")
    prompts = agent_actions(a, block.line + 2)
    assert list(prompts) == [
        f"Discuss {block.sha[:8]} with agent",
        "Discuss all messages with agent",
    ]
    one, every = prompts.values()
    assert one == a.agent_prompt(block) and every == a.agent_prompt()

    assert f"{a.full_sha(block)} (the block at line {block.line + 1}) in `REWORD_EDITMSG`" in one
    assert f"repository at `{repo}`" in one and str(edit_file) not in one
    assert f"{block.sha} Second commit" in one
    assert a.result.blocks[0].sha not in one, "only the focused commit is listed"
    assert all(f"{b.sha} " in every for b in a.result.blocks)
    for prompt in (one, every):
        assert fmt.AGENT_GUIDE.rstrip("\n") in prompt
        assert "- Change only message lines.\n" in prompt
        assert "Do not run `git reword`" in prompt
        assert "\n\n\n" not in prompt, "Zed would collapse it"

    # In the header, outside any block: only the file-wide action.
    assert list(agent_actions(a, 0)) == ["Discuss all messages with agent"]


def test_agent_actions_need_known_commits_and_a_root(edit_file: Path, repo: Path):
    text = edit_file.read_text()
    first, second, _ = fmt.parse(text).blocks
    # One unknown sha: no action for that block, and still two known ones.
    unknown = text.replace(f"commit {first.sha}", "commit deadbeef")
    a = Analysis(edit_file.as_uri(), unknown, Repo.discover(edit_file), client="Zed")
    prompt = a.agent_prompt()
    assert prompt is not None and "deadbeef" not in prompt
    assert agent_actions(a, first.line) == {"Discuss all messages with agent": prompt}
    # Only one known commit left: the file-wide action would repeat the per-block one.
    unknown = unknown.replace(f"commit {second.sha}", "commit deadbee0")
    a = Analysis(edit_file.as_uri(), unknown, a.repo, client="Zed")
    assert list(agent_actions(a, a.result.blocks[2].line)) == [
        f"Discuss {a.result.blocks[2].sha[:8]} with agent"
    ]

    # No worktree root (old edit file under .git/): no agent actions.
    old = repo / ".git" / "REWORD_EDITMSG"
    old.write_text(text)
    a = Analysis(old.as_uri(), text, Repo.discover(old), client="Zed")
    assert agent_actions(a, first.line) == {}


def test_agent_prompt_in_edit_info_mode(info_file: Path):
    a = Analysis(info_file.as_uri(), info_file.read_text(), Repo.discover(info_file), client="Zed")
    prompt = a.agent_prompt()
    assert prompt is not None and "- Change only message lines and info lines.\n" in prompt


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
    assert original.count("#   A  f") == 3
    for subject in ("Second commit", "Third commit"):
        text = original.replace(f"    {subject}", f"    {subject}, edited")
        a = analyse(edit_file, text)
        block = next(b for b in a.result.blocks if a.changed(b))
        actions = a.code_actions(
            lsp.Range(lsp.Position(block.line, 0), lsp.Position(block.line, 0))
        )
        assert apply_edits(text, actions[0].edit.changes[a.uri]) == original


def stat_actions(a: Analysis, line: int, *, lazy: bool = False) -> dict[str, lsp.CodeAction]:
    at = lsp.Range(lsp.Position(line, 0), lsp.Position(line, 0))
    return {
        x.title: x for x in a.code_actions(at, lazy=lazy) if x.title.startswith("Add file stats")
    }


def test_add_stats_gives_the_writers_text(edit_file: Path, repo: Path):
    """Per block, one after the other, and all at once: both end up where
    --stat would have."""
    commits = git.get_commits("HEAD~3..HEAD", cwd=repo)
    expected = fmt.write(commits, stats=git.get_stats("HEAD~3..HEAD", cwd=repo))

    text = edit_file.read_text()
    for i in range(3):
        a = analyse(edit_file, text)
        block = a.result.blocks[i]
        actions = stat_actions(a, block.line + 2)
        action = actions[f"Add file stats to {block.sha[:8]}"]
        text = apply_edits(text, action.edit.changes[a.uri])
    assert text == expected

    a = analyse(edit_file)
    action = stat_actions(a, a.result.blocks[0].line)["Add file stats to all commits"]
    assert apply_edits(a.text, action.edit.changes[a.uri]) == expected
    # Also offered off any block, e.g. on the header.
    assert list(stat_actions(a, 0)) == ["Add file stats to all commits"]


def test_add_stats_not_offered_when_present(edit_file: Path, repo: Path):
    commits = git.get_commits("HEAD~3..HEAD", cwd=repo)
    with_stats = fmt.write(commits, stats=git.get_stats("HEAD~3..HEAD", cwd=repo))
    a = analyse(edit_file, with_stats)
    assert all(not stat_actions(a, b.line) for b in a.result.blocks)

    # One block left without: its own action, and no all-commits one.
    text = with_stats.replace("\n#   1 file changed, 1 insertion(+)\n#   A  f3\n", "")
    a = analyse(edit_file, text)
    last = a.result.blocks[-1]
    assert list(stat_actions(a, last.line)) == [f"Add file stats to {last.sha[:8]}"]
    assert not stat_actions(a, a.result.blocks[0].line)


def test_add_stats_skips_merges(repo: Path):
    git_cmd("checkout", "-q", "-b", "side", "HEAD~1", cwd=repo)
    (repo / "s").write_text("s")
    git_cmd("add", "s", cwd=repo)
    git_cmd("commit", "-q", "-m", "side", cwd=repo)
    git_cmd("checkout", "-q", "main", cwd=repo)
    git_cmd("merge", "-q", "--no-ff", "-m", "merge", "side", cwd=repo)
    path = repo / "REWORD_EDITMSG"
    a = analyse(path, fmt.write(git.get_commits("HEAD~3..HEAD", cwd=repo)))
    merge = next(b for b in a.result.blocks if b.message == "merge")
    assert not any(t.endswith(merge.sha[:8]) for t in stat_actions(a, merge.line))
    action = stat_actions(a, merge.line)["Add file stats to all commits"]
    assert a.full_sha(merge) not in action.data["shas"]


def test_add_stats_lazy(edit_file: Path, repo: Path, monkeypatch: pytest.MonkeyPatch):
    """Offering runs no stat lookup; resolving works on the document as it
    is then, so an edit above the block in between does not misplace it."""
    commits = git.get_commits("HEAD~3..HEAD", cwd=repo)
    stats = git.get_stats("HEAD~3..HEAD", cwd=repo)
    a = analyse(edit_file)
    block = a.result.blocks[1]

    def no_lookup(*args: object, **kwargs: object) -> None:
        raise AssertionError("stat lookup while offering")

    with monkeypatch.context() as m:
        m.setattr(git, "get_commit_stats", no_lookup)
        action = stat_actions(a, block.line, lazy=True)[f"Add file stats to {block.sha[:8]}"]
    assert action.edit is None
    assert action.data == {"action": "add-stats", "uri": a.uri, "shas": [a.full_sha(block)]}

    edited = a.text.replace("    First commit\n", "    First commit\n\n    More body.\n")
    b = analyse(edit_file, edited)
    sha = a.full_sha(block)
    expected = fmt.write(commits, stats={sha: stats[sha]})
    expected = expected.replace("    First commit\n", "    First commit\n\n    More body.\n")
    assert apply_edits(edited, b.stat_edits(action.data["shas"])) == expected
    # Resolving again after it was applied adds nothing.
    assert analyse(edit_file, expected).stat_edits(action.data["shas"]) == []


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
        legend = caps["semanticTokensProvider"]["legend"]
        assert legend == {"tokenTypes": ["variable"], "tokenModifiers": ["constant"]}
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


def test_stdio_resolve_stats(edit_file: Path, repo: Path):
    """A client that resolves `edit` gets the stats action without one, and
    the edit from codeAction/resolve."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "git_reword.lsp.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        c = Client(proc)
        resolve = {"textDocument": {"codeAction": {"resolveSupport": {"properties": ["edit"]}}}}
        init_id = c.send(
            "initialize", {"processId": None, "rootUri": repo.as_uri(), "capabilities": resolve}
        )
        caps = c.wait_for(id_=init_id)["result"]["capabilities"]
        assert caps["codeActionProvider"]["resolveProvider"] is True
        c.send("initialized", {}, request=False)

        uri = edit_file.as_uri()
        text = edit_file.read_text()
        c.send(
            "textDocument/didOpen",
            {"textDocument": {"uri": uri, "languageId": "reword", "version": 1, "text": text}},
            request=False,
        )
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
        action = next(x for x in actions if x["title"] == "Add file stats to all commits")
        assert "edit" not in action

        res_id = c.send("codeAction/resolve", action)
        resolved = c.wait_for(id_=res_id)["result"]
        edits = [
            lsp.TextEdit(
                lsp.Range(lsp.Position(**e["range"]["start"]), lsp.Position(**e["range"]["end"])),
                e["newText"],
            )
            for e in resolved["edit"]["changes"][uri]
        ]
        commits = git.get_commits("HEAD~3..HEAD", cwd=repo)
        stats = git.get_stats("HEAD~3..HEAD", cwd=repo)
        assert apply_edits(text, edits) == fmt.write(commits, stats=stats)

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


@pytest.fixture
def info_file(repo: Path) -> Path:
    commits = git.get_commits("HEAD~3..HEAD", cwd=repo)
    path = repo / "REWORD_EDITMSG"
    path.write_text(fmt.write(commits, commit_info=True, edit_info=True))
    return path


def test_info_line_diagnostics(info_file: Path):
    text = info_file.read_text()
    assert "# git-reword-options: edit-info" in text
    # First block: three bad lines. Second block: a valid author and committer edit.
    text = text.replace("Author:     Test <test@example.com>", "Author:     Test", 1)
    text = text.replace("Author:     Test <test@example.com>", "Author:     Ole <ole@x>", 1)
    lines = text.split("\n")
    # Git's date parser is lenient (approxidate), so the value has to be junk.
    date_line = next(i for i, line in enumerate(lines) if line.startswith("AuthorDate:"))
    lines[date_line] = "AuthorDate: bogus"
    cdate_line = next(i for i, line in enumerate(lines) if line.startswith("CommitDate:"))
    lines[cdate_line] = "CommitDat: bogus"
    commit_lines = [i for i, line in enumerate(lines) if line.startswith("Commit:")]
    lines[commit_lines[1]] = "Commit:     Ole <ole@x>"
    text = "\n".join(lines)
    a = analyse(info_file, text)
    diags = a.diagnostics()
    by_code = {d.code: d for d in diags}
    assert sorted(str(d.code) for d in diags) == sorted(
        ["bad-author", "bad-author-date", "changed", "unknown-info-key"]
    )
    assert lines[by_code["bad-author"].range.start.line] == "Author:     Test"
    assert by_code["bad-author-date"].range.start.line == date_line
    assert lines[by_code["unknown-info-key"].range.start.line].startswith("CommitDat:")
    assert all(d.severity is lsp.DiagnosticSeverity.Error for d in diags if d.code != "changed")
    assert by_code["changed"].message == "Author and committer changed"
    assert by_code["changed"].range.start.line == a.result.blocks[1].line

    # Same edits without the directive: warnings, nothing changed.
    plain = "\n".join(line for line in lines if "git-reword-options" not in line)
    a = analyse(info_file, plain)
    diags = a.diagnostics()
    assert sorted(str(d.code) for d in diags) == ["info-display-only"] * 4 + ["unknown-info-key"]
    assert all(
        d.severity is lsp.DiagnosticSeverity.Warning for d in diags if d.code != "unknown-info-key"
    )
    assert not any(a.changed(b) for b in a.result.blocks)


def test_info_hover_and_revert(info_file: Path):
    original = info_file.read_text()
    a = analyse(info_file)
    assert a.diagnostics() == []
    block = a.result.blocks[0]
    hover = a.hover(lsp.Position(block.line, 3))
    assert hover is not None
    assert "Author: Test <test@example.com> ·" in hover.contents.value
    assert "Commit: Test <test@example.com> ·" in hover.contents.value

    text = original.replace("Author:     Test <test@example.com>", "Author:     Ole <ole@x>", 1)
    text = text.replace("    First commit", "    First commit, edited")
    a = analyse(info_file, text)
    block = a.result.blocks[0]
    assert a.changed(block) and a.changed_parts(block) == ["message", "author"]
    actions = a.code_actions(lsp.Range(lsp.Position(block.line, 0), lsp.Position(block.line, 0)))
    assert actions[0].title == f"Revert {block.sha[:8]} to its original message and author"
    assert apply_edits(text, actions[0].edit.changes[a.uri]) == original

    # A committer-only change: no message edit in the revert.
    text = original.replace("Commit:     Test <test@example.com>", "Commit:     Ole <ole@x>", 1)
    a = analyse(info_file, text)
    block = a.result.blocks[0]
    actions = a.code_actions(lsp.Range(lsp.Position(block.line, 0), lsp.Position(block.line, 0)))
    assert actions[0].title == f"Revert {block.sha[:8]} to its original committer"
    assert len(actions[0].edit.changes[a.uri]) == 1
    assert apply_edits(text, actions[0].edit.changes[a.uri]) == original
    assert [s.detail for s in a.symbols()][0].endswith(" · changed")
