"""Read and write the edit file.

This is still the old delimiter format. It is replaced by the format in
prose/spec/reword-format.md in the next step.
"""

from __future__ import annotations

import re

from git_reword.git import Commit

COMMIT_DELIMITER = "=== COMMIT: {} ==="
END_DELIMITER = "=== END COMMIT ==="

_BLOCK_RE = re.compile(
    re.escape(COMMIT_DELIMITER.format("PLACEHOLDER")).replace("PLACEHOLDER", "([0-9a-f]+)")
    + "(.*?)"
    + re.escape(END_DELIMITER),
    re.DOTALL,
)


def write(commits: list[Commit], *, repo_url: str | None, commit_link: bool) -> str:
    lines = [
        "# Edit commit messages below",
        "# - You can modify commit messages freely",
        "# - Do NOT change the COMMIT: <SHA> lines",
        "# - Do NOT reorder commits",
        "# - Do NOT add or remove commits",
        "# - Lines starting with # are ignored",
        "",
    ]
    for commit in commits:
        lines.append(COMMIT_DELIMITER.format(commit.sha))
        if repo_url and commit_link:
            lines.append(f"# {repo_url}/-/commit/{commit.sha}")
        lines.append(commit.full_message)
        lines.append(END_DELIMITER)
        lines.append("")
    return "\n".join(lines)


def parse(content: str) -> dict[str, str]:
    """Map sha -> edited message. Blocks with an empty message are omitted."""
    edited: dict[str, str] = {}
    for match in _BLOCK_RE.finditer(content):
        sha = match.group(1)
        message = match.group(2).strip()
        message_lines = [line for line in message.split("\n") if not line.startswith("#")]
        final_message = "\n".join(message_lines).strip()
        if final_message:
            edited[sha] = final_message
    return edited
