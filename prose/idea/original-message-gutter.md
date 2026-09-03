# Git-gutter style view of what changed

Jotted 2026-09-03.

It would be awesome if the edit buffer was anchored to the original
messages, so the editor shows "git gutter" marks for changed, added and
removed lines. Today the only signal is the `changed` hint diagnostic on the
commit line, plus hover to see the original.

## Routes

- **Real git gutter.** Zed's gutter comes from the buffer's own repository
  diff. The edit file lives in `$GIT_DIR`, which is not a worktree, so
  nothing to diff against. A scratch repo (`$GIT_DIR/reword/` with the
  pristine file committed, edit file as its working copy) would give the
  native gutter for free, but Zed finds a repository by walking up for
  `.git`, and a `.git` inside the real `.git` is asking for trouble. Worth
  a quick experiment before dismissing.
- **LSP diagnostics.** Per-line `Hint` diagnostics for lines that differ
  from the original (or `Information` with the original text as message).
  Cheap, works in every editor, but visually noisy and no "removed line"
  marker.
- **Inlay hints / semantic tokens.** A `changed` semantic token modifier on
  modified lines, or an inlay hint at the end of a changed subject. Editor
  support for styling modifiers varies.
- **Diff view on demand.** A code action "Diff <sha> against original" that
  returns a `WorkspaceEdit`-free command; the server writes the original
  block to a temp file and asks the editor to open it. Zed can then show a
  side-by-side via its own diff actions. Not a gutter but close.

The parser already gives block ranges, and `Analysis.original()` and
`message_lines()` give both sides, so the diff itself is trivial; the whole
question is how to surface it.
