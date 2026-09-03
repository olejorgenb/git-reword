"""Apply new commit messages with an interactive rebase."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from git_reword.git import Commit, GitError, run


def changed_commits(commits: list[Commit], edited: dict[str, str]) -> list[tuple[Commit, str]]:
    """(commit, new message) for every commit whose message differs."""
    changes = []
    for commit in commits:
        new_message = edited.get(commit.sha, "")
        if new_message and new_message != commit.message:
            changes.append((commit, new_message))
    return changes


def apply(commits: list[Commit], edited: dict[str, str]) -> bool:
    """Rebase with GIT_SEQUENCE_EDITOR / GIT_EDITOR scripts that reword.

    Runs with commit.cleanup=whitespace so `#` lines in messages survive.
    """
    if not commits:
        return True

    first_sha = commits[0].sha
    try:
        parent_sha = run("rev-parse", f"{first_sha}^")
    except GitError:
        print(f"Error: Cannot find parent of {first_sha}")
        print("This might be the root commit.")
        return False

    changes = changed_commits(commits, edited)
    reword_shas = [c.sha for c, _ in changes]
    # GIT_EDITOR only sees the message; pairs are matched and consumed in
    # order (oldest first, same as the rebase replay) so two commits sharing
    # an original message each get their own new one.
    pairs = [[c.message, new] for c, new in changes]

    with tempfile.TemporaryDirectory(prefix="git-reword-") as tmpdir:
        tmp = Path(tmpdir)
        seq_editor_path = tmp / "sequence_editor.py"
        msg_editor_path = tmp / "message_editor.py"
        state_path = tmp / "state.json"

        seq_editor_path.write_text(_sequence_editor(reword_shas), encoding="utf-8")
        msg_editor_path.write_text(_message_editor(state_path), encoding="utf-8")
        state_path.write_text(json.dumps(pairs), encoding="utf-8")

        python = shlex.quote(sys.executable)
        env = os.environ.copy()
        env["GIT_SEQUENCE_EDITOR"] = f"{python} {shlex.quote(str(seq_editor_path))}"
        env["GIT_EDITOR"] = f"{python} {shlex.quote(str(msg_editor_path))}"

        print("Applying changes via rebase...")
        result = subprocess.run(
            ["git", "-c", "commit.cleanup=whitespace", "rebase", "-i", parent_sha], env=env
        )
        return result.returncode == 0


def _sequence_editor(reword_shas: list[str]) -> str:
    return f"""#!/usr/bin/env python3
import sys

with open(sys.argv[1]) as f:
    lines = f.readlines()

reword_shas = {reword_shas!r}

new_lines = []
for line in lines:
    if line.strip() and not line.startswith('#'):
        parts = line.split(None, 2)
        if len(parts) >= 2:
            short_sha = parts[1]
            if any(full.startswith(short_sha) for full in reword_shas):
                line = line.replace('pick', 'reword', 1)
    new_lines.append(line)

with open(sys.argv[1], 'w') as f:
    f.writelines(new_lines)
"""


def _message_editor(state_path: Path) -> str:
    # The file git hands us is the original message followed by git's own
    # comment block. Messages may contain `#` lines, so match by prefix
    # against the known originals instead of splitting on `#`. Pairs are
    # consumed in order and the remainder is saved back to the state file,
    # so two commits with the same original message each get their own
    # new message.
    return f"""#!/usr/bin/env python3
import json
import sys

msg_file = sys.argv[1]
state_file = {str(state_path)!r}

with open(msg_file, encoding="utf-8") as f:
    content = f.read()

with open(state_file, encoding="utf-8") as f:
    pairs = json.load(f)

for i, (original, new) in enumerate(pairs):
    if content == original or content.startswith(original + '\\n'):
        with open(msg_file, 'w', encoding="utf-8") as f:
            f.write(new + '\\n')
        del pairs[i]
        with open(state_file, 'w', encoding="utf-8") as f:
            json.dump(pairs, f)
        sys.exit(0)

print('git-reword: could not match the commit being reworded to an edited message',
      file=sys.stderr)
print('git-reword: aborting; run `git rebase --abort` to restore', file=sys.stderr)
sys.exit(1)
"""
