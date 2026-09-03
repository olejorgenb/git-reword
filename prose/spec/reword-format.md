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
- Comments are allowed anywhere: before the first block, between blocks, and
  between the `commit` line and the message.
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
line, with a comment header explaining the rules.

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

The tool writes the file to `$GIT_DIR/REWORD_EDITMSG`, following the
`COMMIT_EDITMSG` / `MERGE_MSG` convention. Rationale:

- The language server finds the repository by walking up from the file path,
  like any LSP finds its project root. The file carries no repo path.
- Editors match the language on the path suffix `REWORD_EDITMSG`.
- Opening the file in an editor that already has the repo open puts it in
  that project, so the language server starts with the repo as root.
- The path is predictable after an abort, which enables `--continue`.

Files elsewhere use the `.reword` extension. The language server then walks
up from the file looking for a repo and degrades gracefully if none is found.

## Consumers

- tree-sitter grammar: nodes `commit`, `sha`, `info_line`, `subject`,
  `message_line`, `comment`. Outline shows sha and subject. Message lines are
  injected as Git Commit (combined injection) for subject/trailer highlighting.
- Language server: diagnostics for every error above plus advisory ones
  (subject length, non-blank second line), hover with the original message,
  code actions (revert to original, open in forge, indent selection), document
  symbols, formatting (fix indentation).
