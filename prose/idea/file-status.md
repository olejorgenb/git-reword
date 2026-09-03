# File status per commit in the edit file

Jotted 2026-09-04.

Optionally list the files each commit touched, for context while rewording.
The block should be foldable.

## Shape

Comment lines under the `commit` line, the same vehicle as `--commit-link`.
Column-0 `#` lines are ignored on read back, so the parser, the resolver
and the apply step need no change.

```
commit 7dcfdad
# M  src/git_reword/cli.py
# A  tests/test_diff.py
# R  old/name.py -> new/name.py

    Subject
```

Name-status (`git show --name-status`) is compact and reads well in a
fixed-width comment. Per-file counts (`--numstat`) or `--stat` bars could
be a variant later.

## Folding

The LSP already gives two folds per commit. Add one over the run of `#`
lines directly under the `commit` line, kind `comment`, placeholder like
`· 3 files`. LSP cannot fold by default, so a long range opens with every
list expanded; hence opt-in.

## Things to decide

- **Position.** Under the `commit` line with the link comment, before the
  info lines and the blank margin. The spec's writing section already
  allows comments there.
- **Turning it on.** A `--stat` flag first. A git config key such as
  `reword.stat` would suit a preference that is the same every run, and
  would be the tool's first config key; the same question applies to
  `--commit-link` and `--info`, so decide once.
- **Merges.** First-parent diff, or nothing. Not a goal.
- **Hover.** The commit-line hover could always carry the file list, at no
  cost to the file. Cheap to do both.
- **Highlighting.** The grammar sees one `comment` node, so the list is
  comment-coloured. A distinct node for status comments is a grammar
  change for cosmetics only.
