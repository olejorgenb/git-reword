# Language server code actions

Companion to `reword-format.md`. Describes what the language server offers
beyond diagnostics and formatting, and how "open this commit" reaches an
editor or a forge.

## Actions

Offered by `textDocument/codeAction` for the block under the cursor (or the
lines in the selection).

| Title | Kind | When |
|---|---|---|
| Indent line(s) | quickfix | selection contains `short-indent` / `unindented-line` diagnostics |
| Revert `<sha>` to its original message | refactor.rewrite | message differs from the original |
| Reflow paragraph | refactor.rewrite | cursor is in a body paragraph (see Reflow) |
| Open `<sha>` in Zed | (command) | client is Zed |
| Open `<sha>` in browser | (command) | a forge URL can be derived from `origin` |

The first two exist today. The last one exists but does not work in Zed.

## Opening a commit

Two targets:

- **Forge.** `https://<host>/<path>/commit/<sha>` (GitHub) or
  `/-/commit/<sha>` (GitLab and others), derived from the `origin` remote as
  today. Unknown hosts get the GitLab shape, which is the best guess.
- **Zed.** `zed://git/commit/<sha>?repo=<worktree-root>` opens Zed's commit
  view: message, stats, full diff, and a button that opens the commit on
  the remote. The `repo` query parameter is the worktree root, URL-encoded.
  Zed opens or focuses a workspace for that path, so the view lands in the
  project window even when the edit file was opened as a single-file
  workspace. This is the same URL the `gl` shell alias in
  `~/config/zsh/git-aliases.zsh` emits as terminal hyperlinks, so the
  desktop scheme handler route is known to work here.

The worktree root comes from the discovered git dir: `git_dir/gitdir` exists
for linked worktrees and names `<root>/.git`; otherwise the root is the
parent of the git dir.

### Mechanism

The server's `git-reword.openCommit` command takes one URL argument. Zed
does not implement `window/showDocument`, so the current implementation is
a silent no-op there. The command therefore opens the URL itself:

1. If the client advertised `window.showDocument` support at initialize,
   use `window/showDocument` with `external: true`. This is what Neovim and
   VS Code do and it respects the editor's own URL handling.
2. Otherwise run an opener as a subprocess, no shell: for `zed://` URLs the
   `zed` CLI (`zed <url>`), which routes the request into the running Zed;
   for anything else `xdg-open` on Linux and `open` on macOS. Failure to
   spawn is reported with `window/showMessage`, never raised.

The "Open in Zed" action is offered only when `clientInfo.name` at
initialize is `Zed`, since no other editor understands the scheme. Both
actions use `CodeActionKind.Empty` so editors list them without filtering.

The document link on each sha keeps pointing at the forge URL. Editors open
links themselves, and a `zed://` link would depend on the desktop's default
handler for the scheme, which is wrong for a dev build of Zed.

## Reflow

Rewrap the paragraph under the cursor to 72 columns of text, that is 76
columns including the 4-space indent. Lines are joined on single spaces,
then broken at whitespace; words longer than the limit stay on their own
line. The edit replaces the paragraph's lines only.

A paragraph is a maximal run of consecutive message lines with no blank
line or comment between them, as in the grammar. The action is not offered
when the cursor is on:

- the subject line, which is never reflowed (`subject-too-long` stays a
  warning for a human);
- a trailer block, that is the last paragraph when every line matches
  `Key: value`;
- a paragraph where any line is indented beyond 4 spaces, treated as
  preformatted (lists, quotes, code);
- a comment, an info line, or a commit line.

The action is offered even when the paragraph is already wrapped, so it can
be used to join lines after editing; if the edit would be a no-op it is
omitted.

Formatting (`textDocument/formatting`) stays whitespace-only and never
reflows. Rewrapping changes line breaks the author may have chosen, so it
stays an explicit action. In Zed, `editor: rewrap` does the same thing when
`allow_rewrap` is `anywhere` and `preferred_line_length` is 76, but it does
not know about subjects and trailers; the server action does.

## Not in scope

Moving commits, diffing against the original, and folding are separate
ideas under `prose/idea/`.
