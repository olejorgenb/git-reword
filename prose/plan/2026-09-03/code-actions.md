# Plan: code actions (open in Zed, URL opening, reflow)

Implements `prose/spec/lsp-code-actions.md`. Four steps, each a commit.
Existing tests keep passing after every step.

## 1. Server-side URL opening

`src/git_reword/lsp/server.py`

- Capture the client at initialize: `@server.feature(lsp.INITIALIZE)` stores
  `params.client_info` on `RewordServer` (pygls 2 calls a user handler after
  its own). `ls.client_capabilities.window.show_document.support` says
  whether `window/showDocument` works.
- New `src/git_reword/lsp/open.py` with `open_url(url) -> str | None`
  returning an error message on failure. Chooses the opener: `zed` CLI for
  `zed://`, else `xdg-open` (Linux) or `open` (Darwin). `subprocess.Popen`
  with a list, stdout/stderr to `DEVNULL`, never `shell=True`. Missing
  executable is the error case.
- `git-reword.openCommit` command: showDocument when supported, otherwise
  `open_url`; an error goes to `window/showMessage` as `Warning`.

Tests (`tests/test_lsp.py`): opener choice per URL and platform with
`Popen` monkeypatched; missing executable reports instead of raising.

## 2. Worktree root and "Open in Zed"

`src/git_reword/lsp/analysis.py`

- `Repo.root`: `git_dir / "gitdir"` exists → parent of the path in it,
  else `git_dir.parent`. Computed in `discover`.
- `Repo.zed_url(sha)`: `zed://git/commit/<sha>?repo=<quoted root>` using
  `urllib.parse.quote`.
- `Analysis` gets a `client: str | None` (the client name); `code_actions`
  offers "Open `<sha>` in Zed" before the browser action when
  `client == "Zed"`. `server.analysis()` passes the stored client name.

Tests: root for a plain repo and for a linked worktree (`git worktree add`
in the fixture); action present only when the client is Zed; URL shape
with a root containing a space.

## 3. Reflow

`src/git_reword/format.py`

- `WIDTH = 72` next to `SUBJECT_MAX`.
- `reflow(lines: list[str], width=WIDTH) -> list[str]`: strip the 4-space
  indent, join, wrap with a small greedy loop (not `textwrap`, which
  mangles double spaces and long words differently than git users expect),
  re-indent. Pure function, easy to test.

`src/git_reword/lsp/analysis.py`

- `paragraph_at(line) -> tuple[int, int] | None`: the run of message
  lines around `line` within the block's message, bounded by blank lines,
  comments, and the block end. Returns `None` on the subject line, when
  any line in the run has more than 4 leading spaces, or when the run is a
  trailer block (last paragraph, every line `Key: value`, using the same
  regex as the grammar: `[A-Za-z][A-Za-z0-9-]*:[ \t]`).
- `code_actions`: "Reflow paragraph" (`RefactorRewrite`) with a single
  `TextEdit` over the paragraph when the result differs.

Tests: reflow joins and wraps at 72; long word kept whole; no action on
subject, trailers, preformatted lines, comments; no action when already
wrapped; edit range covers exactly the paragraph.

## 4. Docs

- `README.md` / `zed-reword/README.md`: list the actions, note that the
  Zed action needs the `zed` CLI on PATH (for a dev build, the one the
  fork installs), and that `showDocument` is used where the editor
  supports it.

## Out of scope

Reflowing from `textDocument/formatting`, forge detection beyond the
current GitHub/GitLab shapes, and the ideas under `prose/idea/`.
