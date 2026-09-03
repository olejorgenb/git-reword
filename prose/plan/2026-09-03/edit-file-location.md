# Plan: edit file at the worktree root

Implements the "File location" change in `prose/spec/reword-format.md`
(commit c259429). Two commits.

## 1. Tool

`src/git_reword/git.py`

- `toplevel(cwd=None) -> Path | None`: `rev-parse --show-toplevel`, `None`
  on `GitError` (bare repo, or cwd inside a git dir).
- `common_dir(cwd=None) -> Path`: `rev-parse --git-common-dir`, resolved
  against cwd since git prints it relative in the main worktree.
- `is_ignored(path, cwd=None) -> bool`: `check-ignore -q`, exit 0 means
  ignored, 1 not, anything else `GitError`.

`src/git_reword/cli.py`

- `edit_file_path() -> Path`: `toplevel() / EDIT_FILE`, falling back to
  `git_dir() / EDIT_FILE`.
- `ensure_excluded(edit_file)`: skip when `is_ignored` or when the file is
  under the git dir. Otherwise read `common_dir/info/exclude` (may not
  exist), and if no line equals `REWORD_EDITMSG`, append
  `# added by git-reword\nREWORD_EDITMSG\n`, creating `info/` if needed.
  Runs before the edit file is written; a failure prints a warning and
  continues, since a stray untracked file is annoying, not dangerous.
- `reword()` uses both. The "Edits preserved at" and `--continue` paths
  need no change beyond the new location.

Tests (`tests/test_cli.py`): edit file appears at the root during editing
and is gone after; `info/exclude` gains the entry once and only once across
two runs; no entry when `.gitignore` already covers it; a linked worktree
(`git worktree add`) gets the file at its own root and the exclude in the
main repo's `.git/info/exclude`.

## 2. Language server and docs

`src/git_reword/lsp/analysis.py`

- `Repo.discover` unchanged in behaviour; drop the "works from inside
  `.git/`" comment or keep it, since `.reword` files can still be anywhere.
- Add `Repo.root: Path | None` from `git.toplevel(cwd=directory)`. This
  replaces the `gitdir`-file logic planned in `code-actions.md` step 2; that
  plan's `Repo.root` bullet is superseded by this one.

`tests/test_lsp.py`: the `edit_file` fixture writes to `repo / EDIT_FILE`;
the discovery test asserts `git_dir` and `root`; keep one test that
discovery still works for a file inside `.git/`, since old edit files may
exist.

`README.md`, `zed-reword/README.md`, `prose/spec/lsp-code-actions.md`
(worktree-root sentence): describe the new location and the exclude
handling.
