# Language server code actions

Companion to `reword-format.md`. Describes what the language server offers
beyond diagnostics and formatting (code actions, links, folding), and how
"open this commit" reaches an editor or a forge.

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

The worktree root is `git rev-parse --show-toplevel` from the edit file's
directory, which the file location rule in `reword-format.md` guarantees is
inside a worktree. The action is not offered when there is no root (a
`.reword` file outside any worktree, or an old edit file under `.git/`).

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
initialize starts with `Zed` (the name carries the release channel: `Zed`,
`Zed Preview`, `Zed Nightly`, `Zed Dev`), since no other editor understands
the scheme. Both
actions use `CodeActionKind.Empty` so editors list them without filtering.

The document link on each sha (ctrl-click in Zed) targets the Zed commit
URL when the client is Zed, otherwise the forge URL. Editors open links
themselves: Zed hands unknown schemes to the desktop's default handler for
`zed://`, which for a dev build must be the dev build (the fork's install
script registers it). The tooltip says "Open in Zed" or "Open in browser".

Paths in a `--stat` block get a document link too: a comment line of the
form `#<spaces><letter>  <path>` links the path to `file://<worktree root>/<path>`
when that file exists. For a rename or copy, `old -> new`, only the new
path is linked. The server recognises the shape, not the origin, so a
hand-written comment of that shape links as well; no worktree root, no
links.

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

## Folding

Editors that fold by indentation hide every indented line under a `commit`
line, subject included, so a folded file is a list of shas. The server
answers `textDocument/foldingRange` with these ranges instead:

- **Block.** From the `commit` line to the last non-blank line of the
  block, with `collapsedText` set to `· ` plus the subject. Folded, the
  block is one line: the `commit` line followed by the subject as the fold
  placeholder. The separator is there because editors trim the placeholder
  text, so a leading space would not keep it off the sha.
- **Body.** From the subject line to the same end line, so the `commit`
  line, info lines and subject stay visible and only the body folds. Not
  offered when the block has no body.
- **Comments.** Every run of two or more consecutive comment lines,
  anywhere in the file, folds from the end of its first line to the end of
  its last, kind `comment`, no `collapsedText`. Folded, the run is its
  first line. This is what turns a `--stat` block into its summary line,
  and the file header into its first line; the server does not know or
  care which comments came from `--stat`. Blank lines end a run.

The block and body ranges are `region` folds. In an editor whose fold
command picks the nearest enclosing range (Zed does), folding from the body
hides the body, folding from a comment run hides that run, and folding from
the `commit` or info lines collapses the whole block, comment runs
included. Trailing blank lines between blocks are left out so the
separation survives folding.
A block with no message gets only the block range, without placeholder.

Zed uses these ranges only when `document_folding_ranges` is `on` for the
language; otherwise it folds by indentation and never asks.

## Not in scope

Moving commits, diffing against the original, and folding are separate
ideas under `prose/idea/`.
