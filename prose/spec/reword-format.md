# Reword file format

The file `git-reword` opens in the editor. Also the input to the tree-sitter
grammar and the language server.

## Example

```
# git-reword: edit the indented messages. Column-0 lines are structure.
# Do not edit, reorder, add or remove `commit` lines.

commit 7dcfdad1afb39b697a8632f0c450c555abe7d5b6
# https://gitlab.com/group/repo/-/commit/7dcfdad1afb39b697a8632f0c450c555abe7d5b6
    test-env-cli: refactor the CLI interface

    The previous subcommand-based interface made it difficult to
    perform multiple operations in a single invocation.

    ai-agent: Claude 4 Opus
    autonomy: med

#   2 files changed, 40 insertions(+), 12 deletions(-)
#   M  src/test_env_cli/cli.py
#   A  tests/test_cli.py

commit a0747cfb245789a748922cd9f354a9ad2ce6d6d8
Author:     Ole Jørgen Brønner <ole@example.com>
AuthorDate: 2026-02-25 05:59:29 +0100
Commit:     Ole Jørgen Brønner <ole@example.com>
CommitDate: 2026-02-25 06:02:11 +0100
    test-env-cli: Refactor time range parsing and improve defaults
```

## Principles

- Looks like `git log` output. Structure at column 0, message content indented.
- Every line is classified by its first character(s). No delimiters to match,
  no lookahead, no escaping.
- Message content round-trips verbatim (modulo git's own whitespace cleanup).
  Lines starting with `#` inside a message are content, not comments.
- The sha is the only identity. Everything else about a commit is derivable
  from git, so the file never needs to carry it.

## Line classification

| Line                                    | Meaning                          |
| --------------------------------------- | -------------------------------- |
| `commit <sha>`                          | Starts a commit block            |
| `# ...`                                 | Comment, ignored                 |
| `<Key>: <value>` (column 0)             | Info line, belongs to the block  |
| 4 spaces or 1 tab, then anything        | Message line, indent stripped    |
| Whitespace only                         | Blank message line               |
| Anything else at column 0               | Error                            |

Details:

- `<sha>` is a full 40 (or 64) hex sha, or an abbreviation of at least 4
  hex digits (git's minimum). The parser accepts any such token; whether
  it names a commit, and which one, is decided by whoever reads the file
  back (see Reading back). The tool writes abbreviated shas by default
  (`--abbrev`, git's own `%h` length, so `core.abbrev` applies) and full
  ones with `--no-abbrev`.
- Comments are allowed on any line, including between message lines. This is
  also what keeps the tree-sitter grammar conflict-free: everything after the
  subject up to the next `commit` line belongs to that message, so blank lines
  and comments never have two possible owners.
- Info lines may only appear between the `commit` line and the first
  message line. The keys are those of `git log --pretty=fuller`: `Author`,
  `AuthorDate`, `Commit`, `CommitDate`. They are emitted behind flags
  (see Writing) and read back by key. An unknown key is an error.
  Whether the lines are context or editable is decided by the file's
  options (next item): without `edit-info` they are context, and editing
  one is a warning, not an error, so a stray edit does not block a
  reword. With `edit-info` every info line present in a block is
  applied as written to that commit when it is rewritten.
- Options lines, `# git-reword-options: <option> ...`, are comments to
  the grammar and to a reader, and a directive to the tool. They may
  appear anywhere before the first `commit` line. The tool writes one
  when a flag changes how the file is read back, so the language server,
  which is started by the editor, and `--continue`, which reopens an old
  file, see the same file the same way. The only option is `edit-info`.
  An unknown option is an error.
- The indent is exactly 4 spaces or exactly 1 tab. Any remaining leading
  whitespace is content. 1-3 leading spaces is an error.
- Blank lines are whitespace-only lines, so editors that strip trailing
  whitespace do not change the message.
- The message is the sequence of message and blank lines from the first
  message line up to the next column-0 line that is not blank. Leading and
  trailing blank lines are trimmed. Trailing whitespace on each line is
  trimmed. This matches git's `--cleanup=whitespace`.
- The first message line is the subject.
- A block with no message lines is an error (empty message).
- The same sha appearing twice is an error.

## Structure

```
document   := (comment | blank | commit)*
commit     := commit_line (comment | info_line)* message
commit_line:= "commit" " " sha
info_line  := key ":" value
message    := subject (message_line | blank)*
```

Blank lines between blocks attach to nothing. A parser may attach them to the
preceding message and trim; the result is the same.

## Writing

The tool writes blocks in range order (oldest first), separated by one blank
line, with a comment header explaining the rules. Within a block the
`commit` line and any comment or info lines come first, then one blank
line, then the message, as in `git log`. The blank line is only a margin:
blank lines before the subject are not part of the message, so a block
without one still parses, and the language server's revert action leaves
the margin in place. A `--stat` block follows the message after one blank
line, as in `git log --stat`; it is not part of the message either, and
edits to the message leave it alone.

Flags:

- `--commit-link`: emit a forge URL as a comment under each `commit` line. Off
  by default, since the language server links each sha.
- `--stat`: emit the files each commit touched as comments after the
  message, separated from it by one blank line, where `git log --stat`
  puts them. Context only: the lines are comments, so reading back ignores
  them and nothing in them is editable. Off by default; there is no config
  key, the flag is enough. The block is:
  - one summary line in git's `--shortstat` wording, e.g.
    `#   2 files changed, 40 insertions(+), 12 deletions(-)`, or
    `#   no files changed` for an empty commit;
  - one line per file: the `--name-status` letter without a similarity
    score (`M`, `A`, `D`, `T`, `R`, `C`), two spaces, and the path. Renames
    and copies are written `old -> new`.

  Every line is `#` and three spaces, then the text. The indent sits
  inside the comment so the block lines up with the message while staying
  a comment for the parser and the highlighter.

  Merge commits get no block: their diff depends on which parent you ask
  about, and merges are not a goal. The summary line first lets an editor
  fold the block down to it (see the language server spec).
- `--author-info`: emit `Author:` and `AuthorDate:` info lines.
- `--commit-info`: emit `Commit:` and `CommitDate:` info lines, the
  committer pair. Useful next to `--author-info` to see the two
  identities side by side.

  Values are aligned like `git log --pretty=fuller`: the key, a colon, and
  spaces up to column 12.
- `--edit-info`: make the info lines editable. Writes the
  `# git-reword-options: edit-info` line and a header that says the info
  lines are applied as written. Implies `--author-info`, since alone it
  would have nothing to edit; `--commit-info` is still opt-in, so
  `--edit-info` by itself edits authors and lets git stamp rewritten
  commits with the current committer and time, as any rewrite does,
  while `--edit-info --commit-info` keeps or edits the committer too.
- `--abbrev` / `--no-abbrev`: abbreviated (default) or full shas on the
  `commit` lines. Abbreviation is git's `%h`, unique within the repository
  at the time of writing. Comments and URLs always carry the full sha.

## Reading back

The tool re-parses the file. Validation errors are reported with line numbers.
Each block's sha is resolved against the commits of the range by prefix: no
match is an unknown commit, more than one match is ambiguous, two blocks
resolving to the same commit are a duplicate. All three are errors, so the
resolution never touches git. After resolution the set of commits must
equal the original set, in the original order. A commit is "changed" when
its cleaned message differs from the original message after the same
cleanup, or, with `edit-info`, when an info value differs from the
original after normalisation. Without `edit-info` the info lines never
make a commit changed; an edited one is reported as a warning and the
original metadata is kept.

With `edit-info`, info values are validated by git, not by the tool: a
line whose text differs from what was written is passed through `git var
GIT_AUTHOR_IDENT` or `GIT_COMMITTER_IDENT` with the matching
`GIT_*_NAME`, `GIT_*_EMAIL` and `GIT_*_DATE` set, which rejects a
malformed date or an empty name and otherwise returns the canonical
ident and timestamp. The name and email are split on the last ` <`; a
value without `<...>` is an error before git is asked. A date written
with a different offset is a change, since git stores the offset.

What a rewritten commit gets, with `edit-info`: every info line present
in its block, edited or not. A key absent from the block, and every
commit rewritten without a block (a descendant after the range), gets
git's default for it: the original author and author date, the current
user and time as committer. So an unchanged `Commit:` line is not a
change by itself, but it is what the commit keeps if it is rewritten for
another reason, where a file without the line would let git stamp it.

The language server resolves shas through git instead (`git log -n 1`),
since it has no range to match against; an ambiguous abbreviation is then
an unknown commit. Links and URLs use the resolved full sha.

When applying, the tool writes commit objects directly with `commit-tree`,
which does no cleanup of its own, so the cleanup above is the only one and
`#` lines in messages survive. Every changed commit and every commit
descending from one, up to `HEAD`, is re-minted with its original tree
and the metadata described above; `HEAD` is then moved once with
`update-ref`,
which fails if `HEAD` moved meanwhile. Unchanged commits before the first
change keep their sha. The index and working tree are never touched. See
`prose/plan/2026-09-06/commit-tree-apply.md`.

## File location

The tool writes the file to `<worktree root>/REWORD_EDITMSG`, the top level
of the working tree the command runs in. Rationale:

- The language server finds the repository by walking up from the file path,
  like any LSP finds its project root. The file carries no repo path.
- Editors match the language on the path suffix `REWORD_EDITMSG`.
- Opening the file in an editor that already has the repo open puts it in
  that project as an ordinary buffer, so the language server starts with the
  repo as root and the editor treats it like any project file.
- The path is predictable after an abort, which enables `--continue`.
- In a linked worktree the file sits next to that worktree's `.git` file,
  where the user is, not in the main repository's `.git/worktrees/<name>/`.

The file used to live in `$GIT_DIR`, like `COMMIT_EDITMSG`. That put it
where editors watch `.git` for changes (Zed opens a lone file as a worktree
that watches its parent directory), so every git operation woke the editor,
and after the tool deleted the file Zed kept polling a stale worktree.

The file is transient and must not be committed. Before writing it, the tool
runs `git check-ignore`; if the path is not ignored it appends
`REWORD_EDITMSG` with a marker comment to `info/exclude` in the common git
dir (`git rev-parse --git-common-dir`), so linked worktrees share it. Users
who prefer `.gitignore` can add it there and the tool leaves `info/exclude`
alone. Without a working tree (bare repository) the tool falls back to
`$GIT_DIR/REWORD_EDITMSG`.

Files elsewhere use the `.reword` extension. The language server then walks
up from the file looking for a repo and degrades gracefully if none is found.

## Consumers

- tree-sitter grammar: nodes `commit`, `sha`, `info_line`, `subject`,
  `message_line`, `comment`, `invalid_line`. No external scanner; `extras` is
  empty and every line rule ends in an explicit newline, so the file must end
  with a newline. `invalid_line` is a lowest-precedence catch-all so a bad
  line is isolated instead of derailing the rest of the block. The subject
  token is split at 72 characters into `subject_text` and `overflow` so the
  overflow can be highlighted. Trailers are matched by a query on `text`.
  Outline shows sha and subject. Injecting the Git Commit grammar per message
  is not possible in Zed because combined injections merge across the whole
  buffer, so message highlighting lives in this grammar.
- Language server: diagnostics for every error above plus advisory ones
  (subject length, non-blank second line), hover with the original message,
  code actions (see `lsp-code-actions.md`: indent, revert to original,
  reflow, add file stats, open in Zed or the forge, discuss with Zed's
  agent), links, document
  symbols, formatting (fix indentation), folding ranges (per block, per
  body, and per run of comment lines, which is what makes a `--stat` block
  collapse to its summary line).
