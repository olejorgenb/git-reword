from __future__ import annotations

from git_reword import format as format_mod
from git_reword.git import Commit

SHA_A = "a" * 40
SHA_B = "b" * 40


def test_round_trip():
    commits = [
        Commit(SHA_A, "First subject", "Body line one\n\nBody line two"),
        Commit(SHA_B, "Second subject", ""),
    ]
    content = format_mod.write(commits, repo_url="https://x/y", commit_link=True)
    edited = format_mod.parse(content)
    assert edited == {
        SHA_A: "First subject\n\nBody line one\n\nBody line two",
        SHA_B: "Second subject",
    }


def test_empty_message_is_omitted():
    content = format_mod.write([Commit(SHA_A, "gone", "")], repo_url=None, commit_link=False)
    content = content.replace("gone", "")
    assert format_mod.parse(content) == {}
