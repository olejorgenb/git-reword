# Plan: new format, tree-sitter grammar, language server, Zed extension

Implements `prose/spec/reword-format.md`. Four pieces, each independently
useful, built in this order so every step is testable on its own.

```
git-reword/
  pyproject.toml
  src/git_reword/
    __init__.py
    cli.py            typer entry point (git-reword)
    format.py         parse/write the reword file, shared by cli and lsp
    git.py            git plumbing helpers
    apply.py          rebase-based apply (later: commit-tree)
    lsp/
      server.py       pygls server (git-reword-lsp)
  tests/
  tree-sitter-reword/ grammar.js, queries/, test/corpus/
  zed-reword/         Zed extension (extension.toml, languages/, src/lib.rs)
```

## Step 1: package the tool

Turn `reword.py` into a uv project per the common conventions (python 3.13,
typer, ruff, ty). Behaviour unchanged in this step; the old delimiter format
still works. Purpose: a place for `format.py` that both cli and lsp import,
and a test suite.

- `pyproject.toml` with scripts `git-reword` and `git-reword-lsp`.
- Move code into `src/git_reword/`, split into `cli.py`, `git.py`,
  `apply.py`. `reword.py` at the root becomes a thin shim or is deleted.
- `DEBUG_SHOW_LOCALS` per CLI conventions.
- User runs `uv sync`.

## Step 2: implement the format

`format.py`:

- `Block(sha, message, line, info: dict)` and `Diagnostic(line, col, severity,
  message, code)`.
- `parse(text) -> ParseResult(blocks, diagnostics)`. Line-at-a-time classifier
  exactly as the spec table. Never raises on bad input; every problem becomes
  a diagnostic with a line number. Error codes: `unindented-line`,
  `short-indent`, `bad-sha`, `duplicate-sha`, `empty-message`,
  `info-after-message`. Advisory codes (for the lsp): `subject-too-long`,
  `second-line-not-blank`, `subject-trailing-period`.
- `cleanup(message)` implementing git's `whitespace` cleanup: strip trailing
  whitespace per line, trim leading/trailing blank lines, collapse runs of
  blank lines. Used for the changed/unchanged comparison.
- `write(commits, *, commit_link, info) -> str`.
- Tests: round-trip of messages with `#` lines, tabs, indented code blocks,
  trailing whitespace; each diagnostic; the prototype example file.

`cli.py`:

- Edit file at `$GIT_DIR/REWORD_EDITMSG` (`git rev-parse --git-dir`).
  Refuse to start if it exists and is non-empty unless `--continue` (reuse it)
  or `--force` (overwrite). Print its path on abort as today.
- Flags `--commit-link` (default off) and `--info` (Author, Date). Drop
  `--no-commit-link`.
- Validation errors printed as `path:line: message` so editors can jump.
- Run rebase with `-c commit.cleanup=whitespace`. Fix the message-editor
  script so it no longer stops at the first `#` line.
- Follow-up, separate plan: replace rebase with a `commit-tree` chain and
  `update-ref`. Not needed for the format change.

## Step 3: tree-sitter grammar

Seed from the prototype in this session's scratchpad (`ts-proto/`). Lives in
`tree-sitter-reword/` in this repo. If Zed cannot load a grammar from a
subdirectory of a `file://` repository, split it into its own repo then.

- `grammar.js` as prototyped: empty `extras`, explicit newlines, `invalid_line`
  catch-all, subject split at 72.
- `queries/highlights.scm`, `queries/outline.scm` (Zed's outline format,
  `@item` / `@name` / `@context`), `queries/indents.scm` if useful.
- `test/corpus/*.txt` with the cases from the prototype example plus the
  error cases. `tree-sitter test` runs in CI-less fashion via a script that
  fetches `tree-sitter-cli` with npx into a project cache dir (the global npm
  cache is read-only in the sandbox).
- Commit generated `src/parser.c` so Zed can build it without node.

## Step 4: language server

Python, `pygls`. Reuses `format.parse` so tool and server never disagree.

Repo discovery: from the document path, `git -C <dir> rev-parse --git-dir`.
Works from inside `.git/` and from a `.reword` file in a worktree. Without a
repo, everything that needs git is silently disabled.

Features, in order of value:

1. Diagnostics: all parse diagnostics, plus advisories. Published on open and
   change, debounced.
2. Document symbols: one per commit, name = subject, detail = short sha.
   Gives Zed's outline even before the grammar is installed.
3. Hover on a sha: original message, author, date. Hover on a subject:
   original subject if changed.
4. Code actions: revert message to original; open commit in forge (URL from
   `origin`, GitLab and GitHub patterns); indent selection by 4 spaces;
   quick-fix for `short-indent` and `unindented-line`.
5. Document links on shas to the forge URL.
6. Formatting: normalise indentation, trim trailing blank lines.

Tests: drive the server through pygls' in-process client, or call the
handlers directly with a fake workspace. At least one test per feature using
a temporary git repo.

## Step 5: Zed extension

`zed-reword/`, installed as a dev extension.

- `extension.toml`: language `Reword`, grammar `reword` via `file://` to this
  repo plus rev, language server `git-reword-lsp`.
- `languages/reword/config.toml`: `path_suffixes = ["REWORD_EDITMSG",
  "reword"]`, `line_comments = ["# "]`, `tab_size = 4`, `hard_tabs = false`.
- `languages/reword/{highlights,outline}.scm` copied from the grammar.
- `src/lib.rs`: `language_server_command` returning `git-reword-lsp` from
  PATH, with a settings override for the binary path. Needs the
  `wasm32-wasip2` target.
- Verify: `git reword --editor 'zed --wait'` inside a repo open in Zed shows
  highlighting, outline, diagnostics, hover.

## Out of scope for now

- Editing `Author:` / `Date:` lines.
- `commit-tree` based apply.
- Neovim filetype and LSP config (easy later; the server is editor-agnostic).
