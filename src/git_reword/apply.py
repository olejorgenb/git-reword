"""Apply new commit messages with an interactive rebase."""

from __future__ import annotations

import os
import subprocess
import tempfile

from git_reword.git import Commit, GitError, run


def changed_commits(commits: list[Commit], edited: dict[str, str]) -> list[tuple[Commit, str]]:
    changes = []
    for commit in commits:
        new_message = edited.get(commit.sha, "")
        if new_message and new_message != commit.full_message:
            changes.append((commit, new_message))
    return changes


def apply(commits: list[Commit], edited: dict[str, str]) -> bool:
    """Rebase with GIT_SEQUENCE_EDITOR / GIT_EDITOR scripts that reword."""
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
    # Matching by original message is what git gives us in GIT_EDITOR.
    message_map = {c.full_message: new for c, new in changes}

    temp_files: list[str] = []
    try:
        seq_editor_path = _write_script(_sequence_editor(reword_shas), temp_files)
        msg_editor_path = _write_script(_message_editor(message_map), temp_files)

        env = os.environ.copy()
        env["GIT_SEQUENCE_EDITOR"] = f"python3 {seq_editor_path}"
        env["GIT_EDITOR"] = f"python3 {msg_editor_path}"

        print("Applying changes via rebase...")
        result = subprocess.run(["git", "rebase", "-i", parent_sha], env=env)
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
    return f"""#!/usr/bin/env python3
import sys

msg_file = sys.argv[1]
with open(msg_file) as f:
    full_content = f.read()

# Message lines come first; git's comments follow.
message_lines = []
for line in full_content.split('\\n'):
    if line.startswith('#'):
        break
    message_lines.append(line)
current_msg = '\\n'.join(message_lines).rstrip('\\n')

message_map = {message_map!r}

if current_msg in message_map:
    with open(msg_file, 'w') as f:
        f.write(message_map[current_msg])
"""
