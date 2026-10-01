# Language server code actions

Companion to `reword-format.md`. Describes what the language server offers
beyond diagnostics and formatting (code actions, links, shas in messages,
folding), and how "open this commit" reaches an editor or a forge.

## Actions

Offered by `textDocument/codeAction` for the block under the cursor (or the
lines in the selection). "Add file stats to all commits" covers the whole
file and is offered from any line.

| Title | Kind | When |
|---|---|---|
| Indent line(s) | quickfix | selection contains `short-indent` / `unindented-line` diagnostics |
| Revert `<sha>` to its original message / author / committer | refactor.rewrite | the block is changed: its message differs, or with `edit-info` an info value does; the title names what changed |
| Reflow paragraph | refactor.rewrite | cursor is in a body paragraph (see Reflow) |
| Add file stats to `<sha>` | refactor.rewrite | the block has no stat block and is not a merge (see Adding file stats) |
| Add file stats to all commits | refactor.rewrite | two or more such blocks in the file |
| Open `<sha>` in Zed | (command) | client is Zed |
| Open `<sha>` in browser | (command) | a forge URL can be derived from `origin` |
| Discuss `<sha>` with agent | (command) | client is Zed and the block is a known commit (see Discussing with Zed's agent) |
| Discuss all messages with agent | (command) | client is Zed and two or more blocks are known commits |

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
does not implement `window/showDocument`, where asking for it would be a
silent no-op, so the command opens the URL itself when it has to:

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

## Shas in messages

A message line can name another commit: "This reverts commit <sha>.",
"fixes a1b2c3d", a forge URL. The token rule is the writer's (see
`reword-format.md`, References to rewritten commits): 7 to 64 lowercase
hex digits with no letter, digit or `_` on either side. Each such token
is looked up the same way `commit` lines are, through the cached commit
lookup. Only message lines are scanned; comment lines are not.

| The token | Gets |
|---|---|
| names a commit | a document link with the same target and tooltip as a `commit` line (Zed's commit view in Zed, otherwise the forge), and a hover: short sha, subject, author and date |
| names the commit of a block that this reword re-mints | also a hint, `sha-rewritten`: "updated to the new sha on apply" |
| names a commit that is not in `HEAD`'s history | also a hint, `sha-not-on-branch`: "not on this branch; rewritten or dropped?" |
| names nothing | nothing; hex words and foreign shas stay quiet |

A block's commit is re-minted when that block or any earlier block is
changed. Blocks are in history order and the set of commits is fixed (see
`reword-format.md`, Reading back), and a commit before the range never
changes. The hint ignores `--no-sha-rewrite`, which the server never
sees; its message says so.

Checking `HEAD`'s history costs one `git merge-base --is-ancestor` per
distinct sha, cached with the commit lookup for the life of the server.
`HEAD` rarely moves while the file is open. When it does, a stale answer
lasts until the server restarts, which is acceptable for a hint.

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

## Adding file stats

Inserts the block `--stat` would have written (see `reword-format.md`,
Writing), for a file written without the flag. The text is the writer's,
produced by the same function: one blank line, the summary line, one line
per file. It goes at the end of the message, before any comment or blank
lines the block already ends with, which is where the writer puts it.

A block already has a stat block when one of the comment lines after its
message is summary-shaped: `#`, whitespace, then `<n> file(s) changed`
or `no files changed`. As with the stat links, the server recognises the
shape and not where it came from. Merge commits get no action, as with
the flag. The per-block action covers the block under the cursor. The
all-commits action covers every block that qualifies, and is offered only
when there are at least two, since with one it would repeat the
per-block action.

Editors ask for code actions on cursor moves, so offering these must not
run git. Whether to offer them is decided from the text and the cached
commit lookup. The actions carry no edit, only `data` naming the
document and the full shas to cover, and `codeAction/resolve` computes
the edit: it analyses the document again from its current text and
inserts into the named blocks that still exist and still lack a stat
block. Clients that do not advertise resolve support for `edit` get the
edit in the `codeAction` response, computed the same way.

## Discussing with Zed's agent

Opens Zed's agent panel on a new thread with a prompt already written, so
the user can work on a message, or on the whole series, together with
the agent. The action runs the open command (see Mechanism) on
`zed://agent?prompt=<text>`. Zed fills the prompt in but never sends
it: the user reads it and presses enter, and Zed marks it as coming from
outside. Zed also removes control characters and turns runs of three or
more newlines into two, so the prompt does not rely on either. The thread
uses whichever agent the panel is set to.

The agent edits the file itself. Its edits go through Zed's buffers, so
they land in the open file even when it has unsaved changes, and Zed
shows them for the user to accept or reject. Nothing comes back through
the server. The edit file sits at the worktree root (see `reword-format.md`,
File location), within the agent's reach; for that reason the prompt
names the file by its path relative to the root.

The prompt is plain text built from the document and the cached commit
lookup, so offering the actions runs no git. It holds:

- the task: improve the message of one commit (sha and line), or the
  messages of the file as a series, discussing before editing;
- the worktree root, the file, and the commits as `<sha> <subject>`
  lines. Diffs are not included: the agent runs `git show` when it needs
  one, and `git log` for the house style. Past 100 commits the list ends
  with a count, since the agent can read the file;
- a short guide to the file's syntax, kept next to the file header in
  the writer so the two change together;
- the limits: only message lines change, plus info lines in `edit-info`
  mode; `commit` lines stay as they are, and commits are not split,
  squashed, reordered, added or dropped; the agent does not run
  `git reword` or commit, since the user finishes the reword;
- a request to fix diagnostics in the lines it touched, if it can see
  them. Zed's built-in agent can; other agents in the panel may not.

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

Moving commits and showing the original message beside the edited one
are separate ideas under `prose/idea/`.
