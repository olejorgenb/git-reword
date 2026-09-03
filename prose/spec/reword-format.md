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

commit a0747cfb245789a748922cd9f354a9ad2ce6d6d8
Author: Ole Jørgen Brønner <ole@example.com>
Date:   2026-02-25 05:59:29 +0100
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

- `<sha>` is a full 40 (or 64) hex sha. Abbreviated shas are an error.
- Comments are allowed on any line, including between message lines. This is
  also what keeps the tree-sitter grammar conflict-free: everything after the
  subject up to the next `commit` line belongs to that message, so blank lines
  and comments never have two possible owners.
- Info lines (`Author:`, `Date:`, ...) may only appear between the `commit`
  line and the first message line. They are emitted behind a flag and are
  ignored on parse for now. Editing them is a possible future extension.
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
the margin in place.

Flags:

- `--commit-link`: emit a forge URL as a comment under each `commit` line. Off
  by default once the language server provides links.
- `--info`: emit `Author:` and `Date:` info lines.

## Reading back

The tool re-parses the file. Validation errors are reported with line numbers.
The set of shas must equal the original set, in the original order. A commit
is "changed" when its cleaned message differs from the original message after
the same cleanup.

When applying, git must be run with `commit.cleanup=whitespace` so that `#`
lines in messages survive.

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
  code actions (revert to original, open in forge, indent selection), document
  symbols, formatting (fix indentation).
