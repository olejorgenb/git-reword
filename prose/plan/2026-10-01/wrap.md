# Plan: code action to wrap long lines in all commits

Spec: `lsp-code-actions.md`, "Wrapping long lines", from commit 538e780.
Branch `wrap`. Replaces a first take, a `--wrap` CLI flag, dropped from
the branch: wrapped when the file was written or after it was saved, the
result was either unasked for in the editor or first seen in the
confirmation diff, with no way to fix a bad wrap but cancelling.

## `format.py`

`wrap_line(text, width=WIDTH) -> list[str]`: one line of message text
(indent stripped) broken greedily at whitespace into pieces of at most
`width`, except a word longer than `width`, which is a piece of its own.
The first piece keeps the text's leading whitespace. A text that fits
comes back as `[text]`.

## `analysis.py`

- `Analysis.wrap_edits() -> list[lsp.TextEdit]`: for each block, the
  message lines after `subject_line`; a line with indented content longer
  than `WIDTH` whose `wrap_line` is more than one piece is replaced by the
  pieces, each behind the line's own indent prefix (`    ` or tab).
- `_wrap_actions()`: "Wrap long lines in all commits", refactor.rewrite,
  with those edits, when there are any. In `code_actions` after the reflow
  action. Not cursor-dependent.

## README

The code actions bullet gets "wrap every long body line in the file".

## Tests (`test_lsp.py`, `test_format.py`)

- `wrap_line`: breaks at spaces, keeps the leading whitespace on the
  first piece, a long word stays whole, a fitting line is unchanged.
- The action on a file with a long body line in two blocks, a long
  subject and a line that is one long URL: one edit per long body line,
  subject and URL line untouched; applying the edits gives the expected
  text. No action when nothing needs wrapping.

One commit.
