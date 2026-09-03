# Git-gutter style view of what changed

Jotted 2026-09-03. Experiment 2026-09-04, see below.

It would be awesome if the edit buffer was anchored to the original
messages, so the editor shows "git gutter" marks for changed, added and
removed lines. Today the only signal is the `changed` hint diagnostic on the
commit line, plus hover to see the original.

## Routes

- **Real git gutter.** Zed's gutter comes from the buffer's own repository
  diff. A scratch repo with the pristine file committed and the edit file
  as its working copy gives the native gutter for free. Verified, see
  below.
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

## Experiment: scratch repository (2026-09-04)

What Zed diffs against: the gutter is the uncommitted diff, buffer versus
`HEAD:<path>` (`git_store.rs`, `load_committed_text`). The index only feeds
the secondary diff that marks hunks as staged. So adding the pristine file
to the index without a commit shows the whole file as added, and a tracked
modified file would also block the interactive rebase and get swept into
`git commit -a` and `git stash`. The base has to be a commit tree, and the
real history is off limits, hence a scratch repository.

Fixture: an outer repo with two commits; a scratch repo holding
`REWORD_EDITMSG` with the pristine text as its only commit and an edited
working copy; the scratch directory listed in the outer `info/exclude`.
Two placements were tried:

- `<root>/.reword/` inside the worktree
- `$GIT_DIR/reword/` outside it

Results:

- Outer `git status` is clean in both cases, so the rebase is unaffected.
- Zed shows the gutter markers in both cases. Inside the worktree this
  holds even though upstream Zed applies the outer repo's `info/exclude`
  to nested repos (worktree.rs, `3cee61d75f5`), so the file is
  `is_ignored` in the project. Discovery of the nested `.git` and the diff
  both still happen.

Open points before adopting it:

- Placement. Inside the worktree keeps the file in the open project, which
  is why it moved out of `$GIT_DIR` in the first place. It does leave the
  buffer ignored, the state that swallowed the folding-range request (see
  the Zed fork's draft notes on LSP folding); check that the language
  server still gets every request it needs. Under `$GIT_DIR` the watch
  only covers `.git/reword/`, so the old noise problem is gone, but it is
  a lone-file worktree again, with the new-window behaviour and the stale
  worktree after deletion.
- The language server finds the repo by walking up from the file, so it
  would find the scratch repo first. It has to recognise it (a marker file
  in the scratch dir, or a config key) and keep walking.
- Zed's git panel lists the scratch repo as a second repository while the
  file is open.
- Lifecycle: create on `git reword`, keep for `--continue`, delete on apply,
  abort and `--force`. The exclude entry becomes the directory instead of
  the file.
- Other editors with a git gutter (vim-gitgutter, VS Code) should get the
  same for free; untested.
