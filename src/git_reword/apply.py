"""Apply new commit messages with an interactive rebase."""

from __future__ import annotations

import os
import subprocess
import tempfile

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
    # GIT_EDITOR only sees the message, so match on the original message.
    message_map = {c.message: new for c, new in changes}

    temp_files: list[str] = []
    try:
        seq_editor_path = _write_script(_sequence_editor(reword_shas), temp_files)
        msg_editor_path = _write_script(_message_editor(message_map), temp_files)

        env = os.environ.copy()
        env["GIT_SEQUENCE_EDITOR"] = f"python3 {seq_editor_path}"
        env["GIT_EDITOR"] = f"python3 {msg_editor_path}"

        print("Applying changes via rebase...")
        result = subprocess.run(
            ["git", "-c", "commit.cleanup=whitespace", "rebase", "-i", parent_sha], env=env
        )
        return result.returncode == 0
    finally:
        for temp_file in temp_files:
            try:
                os.unlink(temp_file)
            except OSError:
                pass


def _write_script(source: str, temp_files: list[str]) -> str:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(source)
        path = f.name
    temp_files.append(path)
    os.chmod(path, 0o755)
    return path


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


def _message_editor(message_map: dict[str, str]) -> str:
    # The file git hands us is the original message followed by git's own
    # comment block. Messages may contain `#` lines, so match by prefix
    # against the known originals, longest first, instead of splitting on `#`.
    return f"""#!/usr/bin/env python3
import sys

msg_file = sys.argv[1]
with open(msg_file) as f:
    content = f.read()

message_map = {message_map!r}

for original in sorted(message_map, key=len, reverse=True):
    if content == original or content.startswith(original + '\\n'):
        with open(msg_file, 'w') as f:
            f.write(message_map[original] + '\\n')
        sys.exit(0)

print('git-reword: could not match the commit being reworded to an edited message',
      file=sys.stderr)
print('git-reword: aborting; run `git rebase --abort` to restore', file=sys.stderr)
sys.exit(1)
"""
