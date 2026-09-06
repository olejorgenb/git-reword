from __future__ import annotations

from pathlib import Path

import pytest

from git_reword import format as format_mod
from git_reword.format import Severity, cleanup, parse, write
from git_reword.git import Commit

DATA = Path(__file__).parent / "data"
SHA_A = "a" * 40
SHA_B = "b" * 40


def codes(result: format_mod.ParseResult) -> list[str]:
    return [d.code for d in result.diagnostics]


def test_round_trip_keeps_hashes_tabs_and_indentation():
    message = "Subject\n\n# not a comment\n- bullet\n    code\n\tless code\n\nKey: value"
    content = write([Commit(SHA_A, message)])
    result = parse(content)
    assert result.errors == []
    assert result.messages == {SHA_A: message}


def test_round_trip_two_commits_in_order():
    content = write([Commit(SHA_A, "First"), Commit(SHA_B, "Second\n\nBody")])
    result = parse(content)
    assert [b.sha for b in result.blocks] == [SHA_A, SHA_B]
    assert result.messages == {SHA_A: "First", SHA_B: "Second\n\nBody"}


INFO_COMMIT = Commit(
    SHA_A,
    "Subject",
    author="Ole <ole@x>",
    author_date="2026-02-25 05:59:29 +0100",
    committer="Bot <bot@x>",
    committer_date="2026-02-26 10:00:00 +0000",
)


def test_write_optional_lines():
    plain = write([INFO_COMMIT])
    assert "Author:" not in plain and "/-/commit/" not in plain
    assert plain.endswith(f"commit {SHA_A}\n\n    Subject\n")  # blank margin, as in git log

    full = write([INFO_COMMIT], repo_url="https://gl/g/r", commit_link=True, author_info=True)
    assert f"# https://gl/g/r/-/commit/{SHA_A}\n" in full
    assert "Author:     Ole <ole@x>\n" in full
    assert "AuthorDate: 2026-02-25 05:59:29 +0100\n\n    Subject\n" in full
    assert "Commit" not in full
    assert full.endswith("\n")

    result = parse(full)
    assert result.errors == []
    assert result.blocks[0].info == {
        "Author": "Ole <ole@x>",
        "AuthorDate": "2026-02-25 05:59:29 +0100",
    }
    assert result.blocks[0].info_lines == {"Author": 5, "AuthorDate": 6}


def test_write_commit_info_and_both():
    committer_only = write([INFO_COMMIT], commit_info=True)
    assert "Author" not in committer_only
    assert "Commit:     Bot <bot@x>\nCommitDate: 2026-02-26 10:00:00 +0000\n\n" in committer_only

    both = write([INFO_COMMIT], author_info=True, commit_info=True)
    info = parse(both).blocks[0].info
    assert list(info) == ["Author", "AuthorDate", "Commit", "CommitDate"]
    assert info["Commit"] == "Bot <bot@x>" and info["CommitDate"] == "2026-02-26 10:00:00 +0000"


def test_tab_indent_and_trailing_whitespace_are_tolerated():
    content = f"commit {SHA_A}\n\tSubject   \n\n\tBody  \n   \n"
    result = parse(content)
    assert result.errors == []
    assert result.messages == {SHA_A: "Subject\n\nBody"}


def test_comments_anywhere_are_ignored():
    content = f"# top\ncommit {SHA_A}\n# under header\n    Subject\n# between\n    Body\n# end\n"
    result = parse(content)
    assert result.errors == []
    assert result.messages == {SHA_A: "Subject\nBody"}


def test_missing_trailing_newline():
    result = parse(f"commit {SHA_A}\n    Subject")
    assert result.errors == []
    assert result.messages == {SHA_A: "Subject"}


def test_empty_message():
    result = parse(f"commit {SHA_A}\n\n# only a comment\n")
    assert codes(result) == ["empty-message"]
    assert result.blocks[0].message == ""


def test_short_indent_is_error_with_line_number():
    result = parse(f"commit {SHA_A}\n    Subject\n  dedented\n    back\n")
    [d] = result.errors
    assert d.code == "short-indent"
    assert d.line == 2
    assert (result.blocks[0].line, result.blocks[0].end_line) == (0, 4)
    # The rest of the block still parses.
    assert result.messages == {SHA_A: "Subject\nback"}


def test_unindented_line_hints_depend_on_position():
    before = parse("stray\n")
    assert codes(before) == ["unexpected-line"]
    assert "`commit <sha>`" in before.diagnostics[0].message

    after_header = parse(f"commit {SHA_A}\nstray\n    Subject\n")
    assert codes(after_header) == ["unexpected-line"]
    assert "info line" in after_header.diagnostics[0].message

    in_message = parse(f"commit {SHA_A}\n    Subject\nstray\n")
    assert codes(in_message) == ["unindented-line"]
    assert "indented by 4 spaces" in in_message.diagnostics[0].message


def test_info_line_after_message_is_error():
    result = parse(f"commit {SHA_A}\n    Subject\nAuthor: x\n")
    assert codes(result) == ["unindented-line"]


def test_bad_and_duplicate_sha():
    # 3 hex digits is below git's minimum abbreviation.
    result = parse(f"commit abc\n    One\ncommit {SHA_A}\n    Two\ncommit {SHA_A}\n    Three\n")
    assert codes(result) == ["bad-sha", "duplicate-sha"]
    assert result.diagnostics[0].col == 7
    assert result.diagnostics[1].line == 4


def test_orphan_message_line():
    result = parse("    Subject\n")
    assert codes(result) == ["orphan-line"]
    assert result.blocks == []


def test_subject_warnings():
    long = "x" * 73
    result = parse(f"commit {SHA_A}\n    {long}\n    second\n")
    assert codes(result) == ["subject-too-long", "second-line-not-blank"]
    assert all(d.severity is Severity.WARNING for d in result.diagnostics)
    assert result.errors == []
    assert result.diagnostics[0].col == 4 + 72

    result = parse(f"commit {SHA_A}\n    Ends with period.\n")
    assert codes(result) == ["subject-trailing-period"]


def test_subject_too_long_column_follows_the_indent():
    """A tab-indented subject is one column wide, not four."""
    long = "x" * 73
    result = parse(f"commit {SHA_A}\n\t{long}\n")
    assert codes(result) == ["subject-too-long"]
    assert result.diagnostics[0].col == 1 + 72


def test_subject_trailing_spaces_do_not_count():
    """Git strips them, so a padded 72 character subject is fine."""
    result = parse(f"commit {SHA_A}\n    {'x' * 72}    \n")
    assert codes(result) == []


def test_example_file():
    result = parse((DATA / "example.reword").read_text())
    assert [b.sha[:8] for b in result.blocks] == ["7dcfdad1", "a0747cfb", "e7ae1a55", "a7405c21"]
    assert result.blocks[0].message.startswith("test-env-cli: refactor the CLI interface\n\n")
    assert "# A markdown heading inside the body" in result.blocks[0].message
    assert "    indented code in the body" in result.blocks[0].message
    assert result.blocks[0].message.endswith("ai-agent: Claude 4 Opus\nautonomy: med")
    assert result.blocks[1].info["committer"] == "someone"
    assert result.blocks[1].message == "tab indented subject line"
    assert [(d.code, d.line) for d in result.diagnostics] == [
        ("subject-too-long", 24),
        ("second-line-not-blank", 25),
        ("short-indent", 29),
        ("second-line-not-blank", 30),
    ]


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("Subject", "Subject"),
        ("\n\nSubject\n\n", "Subject"),
        ("Subject  \n\n\n\nBody \n", "Subject\n\nBody"),
        ("Subject\n\n# kept\nBody", "Subject\n\n# kept\nBody"),
        ("", ""),
        ("   \n\n", ""),
    ],
)
def test_cleanup(raw: str, clean: str):
    assert cleanup(raw) == clean


def test_reflow_joins_and_wraps():
    lines = [
        "    This is a paragraph written with short",
        "    lines that should be joined and wrapped at seventy-two columns of",
        "    text, excluding the indent.",
    ]
    out = format_mod.reflow(lines)
    assert all(len(line) <= 76 and line.startswith("    ") for line in out)
    assert " ".join(line[4:] for line in out) == " ".join(line[4:] for line in lines)
    assert out == [
        "    This is a paragraph written with short lines that should be joined and",
        "    wrapped at seventy-two columns of text, excluding the indent.",
    ]


def test_reflow_keeps_long_words_whole_and_accepts_tabs():
    long_word = "x" * 80
    assert format_mod.reflow(["\tshort", f"\t{long_word}", "    tail"]) == [
        "    short",
        f"    {long_word}",
        "    tail",
    ]
    assert format_mod.reflow(["    a  b   c"]) == ["    a b c"]


def test_write_abbrev():
    commit = Commit(SHA_A, "Subject", short="aaaaaaa")
    assert "commit aaaaaaa\n" in write([commit])
    assert f"commit {SHA_A}\n" in write([commit], abbrev=False)
    # No abbreviation known: fall back to the full sha.
    assert f"commit {SHA_A}\n" in write([Commit(SHA_A, "Subject")])
    # The link comment always carries the full sha.
    linked = write([commit], repo_url="https://gl/g/r", commit_link=True)
    assert f"# https://gl/g/r/-/commit/{SHA_A}\n" in linked


def test_parse_accepts_abbreviated_sha():
    result = parse("commit abcd\n    Four\ncommit abcdef012345\n    Twelve\n")
    assert result.errors == []
    assert [b.sha for b in result.blocks] == ["abcd", "abcdef012345"]


def test_write_stats():
    from git_reword.git import Stat

    stats = {
        SHA_A: Stat(
            "2 files changed, 3 insertions(+), 1 deletion(-)", [("M", "a"), ("R", "b -> c")]
        ),
        SHA_B: None,  # a merge: no block
    }
    content = write(
        [Commit(SHA_A, "First"), Commit(SHA_B, "Second"), Commit("c" * 40, "Third")],
        repo_url="https://gl/g/r",
        commit_link=True,
        stats=stats,
    )
    assert (
        f"commit {SHA_A}\n# https://gl/g/r/-/commit/{SHA_A}\n\n    First\n\n"
        "#   2 files changed, 3 insertions(+), 1 deletion(-)\n#   M  a\n#   R  b -> c\n\n"
        f"commit {SHA_B}\n# https://gl/g/r/-/commit/{SHA_B}\n\n    Second\n\n"
    ) in content
    assert write([Commit(SHA_A, "First")], stats={SHA_A: Stat("", [])}).endswith(
        f"commit {SHA_A}\n\n    First\n\n#   no files changed\n"
    )

    result = parse(content)
    assert result.errors == []
    assert result.messages == {SHA_A: "First", SHA_B: "Second", "c" * 40: "Third"}


def test_edit_info_directive_round_trips():
    text = write([INFO_COMMIT], edit_info=True)
    assert "# git-reword-options: edit-info\n" in text
    assert "Author:     Ole <ole@x>\n" in text, "--edit-info implies the author lines"
    assert "Commit:" not in text
    result = parse(text)
    assert result.errors == [] and result.options == {"edit-info"} and result.edit_info

    plain = parse(write([INFO_COMMIT], author_info=True))
    assert plain.options == set() and not plain.edit_info
    assert "applied as written" not in write([INFO_COMMIT], author_info=True)


def test_options_line_errors():
    result = parse("# git-reword-options: edit-info bogus\ncommit " + SHA_A + "\n    S\n")
    assert [(d.code, d.line) for d in result.errors] == [("unknown-option", 0)]
    assert result.options == {"edit-info"}, "the known one still counts"

    result = parse("commit " + SHA_A + "\n    S\n# git-reword-options: edit-info\n")
    assert [(d.code, d.line) for d in result.errors] == [("misplaced-options", 2)]
    assert not result.edit_info
