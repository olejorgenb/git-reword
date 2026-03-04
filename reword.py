#!/usr/bin/env python3

"""
A tool for bulk editing git commit messages in your editor.

This tool allows you to edit multiple commit messages at once by:
1. Extracting full commit messages (including body) for a given range
2. Opening them in your editor with clear delimiters
3. Applying the changes via interactive rebase

The format in the editor is:
    === COMMIT: <SHA> ===
    <full commit message>
    === END COMMIT ===
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple


@dataclass
class Commit:
    """Represents a git commit with its full message."""
    sha: str
    subject: str
    body: str

    @property
    def full_message(self) -> str:
        """Get the complete commit message."""
        if self.body:
            return f"{self.subject}\n\n{self.body}"
        return self.subject


class GitCommitRewriter:
    """Handles bulk rewriting of git commit messages."""

    COMMIT_DELIMITER = "=== COMMIT: {} ==="
    END_DELIMITER = "=== END COMMIT ==="

    def __init__(self):
        self.original_commits: List[Commit] = []
        self.edited_commits: Dict[str, str] = {}

    def get_commits(self, commit_range: str) -> List[Commit]:
        """Get all commits in the specified range with full messages."""
        try:
            # Get commit SHAs first
            result = subprocess.run(
                ['git', 'rev-list', '--reverse', commit_range],
                capture_output=True, text=True, check=True
            )
            shas = result.stdout.strip().split('\n')

            commits = []
            for sha in shas:
                if not sha:
                    continue

                # Get full commit message
                msg_result = subprocess.run(
                    ['git', 'log', '--format=%B', '-n', '1', sha],
                    capture_output=True, text=True, check=True
                )

                full_msg = msg_result.stdout.rstrip('\n')
                lines = full_msg.split('\n')

                # First line is subject, rest is body
                subject = lines[0] if lines else ''
                body_lines = lines[1:] if len(lines) > 1 else []

                # Remove leading empty lines from body
                while body_lines and not body_lines[0].strip():
                    body_lines.pop(0)

                body = '\n'.join(body_lines) if body_lines else ''

                commits.append(Commit(sha=sha, subject=subject, body=body))

            return commits

        except subprocess.CalledProcessError as e:
            print(f"Error getting commits: {e}")
            if e.stderr:
                print(f"Git error: {e.stderr}")
            return []

    def create_edit_content(self, commits: List[Commit]) -> str:
        """Create the content for the editor with all commit messages."""
        lines = []
        lines.append("# Edit commit messages below")
        lines.append("# - You can modify commit messages freely")
        lines.append("# - Do NOT change the COMMIT: <SHA> lines")
        lines.append("# - Do NOT reorder commits")
        lines.append("# - Do NOT add or remove commits")
        lines.append("# - Lines starting with # are ignored")
        lines.append("")

        for commit in commits:
            lines.append(self.COMMIT_DELIMITER.format(commit.sha))
            lines.append(commit.full_message)
            lines.append(self.END_DELIMITER)
            lines.append("")

        return '\n'.join(lines)

    def parse_edited_content(self, content: str) -> Dict[str, str]:
        """Parse the edited content and extract new messages for each commit."""
        # Pattern to match commit blocks
        # We need to escape the delimiter but keep the capture group
        escaped_start = re.escape(self.COMMIT_DELIMITER.format("PLACEHOLDER"))
        escaped_start = escaped_start.replace("PLACEHOLDER", "([0-9a-f]+)")
        escaped_end = re.escape(self.END_DELIMITER)

        pattern = re.compile(
            rf'{escaped_start}(.*?){escaped_end}',
            re.DOTALL
        )

        edited_messages = {}

        for match in pattern.finditer(content):
            sha = match.group(1)
            message = match.group(2).strip()

            # Remove any comment lines from the message
            message_lines = []
            for line in message.split('\n'):
                if not line.startswith('#'):
                    message_lines.append(line)

            final_message = '\n'.join(message_lines).strip()
            if final_message:
                edited_messages[sha] = final_message

        return edited_messages

    def validate_edited_commits(self, original: List[Commit], edited: Dict[str, str]) -> bool:
        """Validate that all commits are present and accounted for."""
        original_shas = {c.sha for c in original}
        edited_shas = set(edited.keys())

        if original_shas != edited_shas:
            missing = original_shas - edited_shas
            extra = edited_shas - original_shas

            if missing:
                print(f"Error: Missing commits in edited file: {missing}")
            if extra:
                print(f"Error: Unknown commits in edited file: {extra}")

            return False

        return True

    def get_changed_commits(self, original: List[Commit], edited: Dict[str, str]) -> List[Tuple[Commit, str]]:
        """Get list of commits that have changed messages."""
        changes = []

        for commit in original:
            new_message = edited.get(commit.sha, '')
            if new_message and new_message != commit.full_message:
                changes.append((commit, new_message))

        return changes

    def apply_changes(self, commits: List[Commit], edited_messages: Dict[str, str]) -> bool:
        """Apply the commit message changes using interactive rebase."""
        if not commits:
            return True

        # Get the parent of the first commit
        first_sha = commits[0].sha
        try:
            result = subprocess.run(
                ['git', 'rev-parse', f'{first_sha}^'],
                capture_output=True, text=True, check=True
            )
            parent_sha = result.stdout.strip()
        except subprocess.CalledProcessError:
            print(f"Error: Cannot find parent of {first_sha}")
            print("This might be the root commit.")
            return False

        # Create a mapping from original messages to new messages
        # This is more reliable than SHA matching during rebase
        message_map = {}
        temp_files = []

        try:
            for commit in commits:
                if commit.sha in edited_messages and edited_messages[commit.sha] != commit.full_message:
                    # Map the original full message to the new one
                    message_map[commit.full_message] = edited_messages[commit.sha]

            # Create the sequence editor script
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as seq_editor:
                seq_editor.write(self._create_sequence_editor(commits, edited_messages))
                seq_editor_path = seq_editor.name
                temp_files.append(seq_editor_path)

            os.chmod(seq_editor_path, 0o755)

            # Create the commit message editor script
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as msg_editor:
                msg_editor.write(self._create_message_editor(message_map))
                msg_editor_path = msg_editor.name
                temp_files.append(msg_editor_path)

            os.chmod(msg_editor_path, 0o755)

            # Set up environment
            env = os.environ.copy()
            env['GIT_SEQUENCE_EDITOR'] = f'python3 {seq_editor_path}'
            env['GIT_EDITOR'] = f'python3 {msg_editor_path}'

            # Run the rebase
            print(f"Applying changes via rebase...")
            result = subprocess.run(
                ['git', 'rebase', '-i', parent_sha],
                env=env
            )

            return result.returncode == 0

        finally:
            # Clean up temp files
            for temp_file in temp_files:
                try:
                    os.unlink(temp_file)
                except:
                    pass

    def _create_sequence_editor(self, commits: List[Commit], edited_messages: Dict[str, str]) -> str:
        """Create the sequence editor script content."""
        # Get SHAs that need rewording
        reword_shas = []
        for commit in commits:
            if commit.sha in edited_messages and edited_messages[commit.sha] != commit.full_message:
                reword_shas.append(commit.sha)

        return f'''#!/usr/bin/env python3
import sys

# Read the rebase todo file
with open(sys.argv[1], 'r') as f:
    lines = f.readlines()

# SHAs that need rewording
reword_shas = {repr(reword_shas)}

# Update the todo list
new_lines = []
for line in lines:
    if line.strip() and not line.startswith('#'):
        parts = line.split(None, 2)
        if len(parts) >= 2:
            action, short_sha = parts[0], parts[1]
            # Check if this commit needs rewording
            matching_sha = None
            for full_sha in reword_shas:
                if full_sha.startswith(short_sha):
                    matching_sha = full_sha
                    break

            if matching_sha:
                # Change pick to reword
                new_lines.append(line.replace('pick', 'reword', 1))
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)
    else:
        new_lines.append(line)

# Write back
with open(sys.argv[1], 'w') as f:
    f.writelines(new_lines)
'''

    def _create_message_editor(self, message_map: Dict[str, str]) -> str:
        """Create the commit message editor script content."""
        return f'''#!/usr/bin/env python3
import sys

# Read the current commit message
msg_file = sys.argv[1]
with open(msg_file, 'r') as f:
    full_content = f.read()

# Extract just the commit message (remove comment lines)
message_lines = []
for line in full_content.split('\\n'):
    if not line.startswith('#'):
        message_lines.append(line)
    else:
        # Stop at first comment line (git comments come after the message)
        break

# Reconstruct the message without trailing newlines
current_msg = '\\n'.join(message_lines).rstrip('\\n')

# Message mapping from original to new
message_map = {repr(message_map)}

# Check if we have a new message for this commit
if current_msg in message_map:
    # Write the new message
    with open(msg_file, 'w') as f:
        f.write(message_map[current_msg])
# If not found, leave the message unchanged
'''

    def run(self, commit_range: str, editor: Optional[str] = None) -> bool:
        """Main entry point to run the reword process."""
        # Get commits
        commits = self.get_commits(commit_range)
        if not commits:
            print("No commits found in the specified range")
            return False

        print(f"Found {len(commits)} commits to potentially reword")

        # Store original commits
        self.original_commits = commits


        # Create edit content
        edit_content = self.create_edit_content(commits)

        # Open in editor
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write(edit_content)
            temp_file = f.name

        try:
            # Open the editor

            editor = editor or os.environ.get('EDITOR', 'vim')

            # Handle editors with arguments
            if ' ' in editor:
                import shlex
                editor_cmd = shlex.split(editor)
            else:
                editor_cmd = [editor]

            editor_cmd.append(temp_file)

            result = subprocess.run(editor_cmd)
            if result.returncode != 0:
                print("Editor exited with error")
                return False

            # Read and parse edited content
            with open(temp_file, 'r') as f:
                edited_content = f.read()

            edited_messages = self.parse_edited_content(edited_content)

            # Validate
            if not self.validate_edited_commits(commits
, edited_messages):
                return False

            # Check for changes
            changes = self.get_changed_commits(commits, edited_messages)
            if not changes:
                print("No changes detected")
                return True

            # Show changes
            print(f"\nDetected {len(changes)} changed commit(s):")
            for commit, new_msg in changes:
                print(f"\n{commit.sha[:8]}: {commit.subject}")
                print("  ↓")
                new_subject = new_msg.split('\n')[0]
                print(f"  {new_subject}")
                if '\n' in new_msg:
                    print("  (+ body changes)")

            # Confirm
            while True:
                response = input("\nApply these changes? [y/N] ").lower()
                if response == 'n':
                    print("Cancelled")
                    return False
                elif response == 'y':
                    break
                else:
                    print("Invalid input")

            # Apply changes
            return self.apply_changes(commits, edited_messages)

        finally:
            try:
                os.unlink(temp_file)
            except:
                pass

# @commit 64e0d050551cb7893d9335623c1cd935a712befa

def detect_branch_range() -> str:
    """Detect the commit range for the current feature branch using origin/HEAD."""
    try:
        result = subprocess.run(
            ['git', 'symbolic-ref', 'refs/remotes/origin/HEAD'],
            capture_output=True, text=True, check=True
        )
        main_ref = result.stdout.strip()  # e.g. refs/remotes/origin/develop
        main_branch = main_ref.removeprefix('refs/remotes/origin/')
    except subprocess.CalledProcessError:
        print("Error: Could not determine main branch.")
        print("Run: git remote set-head origin --auto")
        sys.exit(1)

    return f'{main_branch}..HEAD'


def main():
    parser = argparse.ArgumentParser(
        description="Bulk edit git commit messages in your editor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                      # Edit all commits on current branch
  %(prog)s abc123               # Edit a single commit
  %(prog)s HEAD~5..HEAD         # Edit last 5 commits
  %(prog)s main..feature        # Edit commits in feature branch
  %(prog)s abc123..def456       # Edit commits in range
        """
    )

    parser.add_argument(
        'range',
        nargs='?',
        help='Git commit range (e.g., HEAD~5..HEAD, main..feature-branch) or single commit (e.g., abc123). Defaults to all commits on the current branch.'
    )
    parser.add_argument(
        '--editor',
        help='Editor to use (defaults to $EDITOR or vim)'
    )

    args = parser.parse_args()

    # Check if in git repository
    try:
        subprocess.run(
            ['git', 'rev-parse', '--git-dir'],
            capture_output=True, check=True
        )
    except subprocess.CalledProcessError:
        print("Error: Not in a git repository")
        sys.exit(1)

    if args.range is None:
        args.range = detect_branch_range()
    elif '..' not in args.range:
        # Single ref: expand to just that commit
        args.range = f'{args.range}^..{args.range}'

    # Run the rewriter
    rewriter = GitCommitRewriter()
    success = rewriter.run(args.range, args.editor)

    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
