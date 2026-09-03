# Folding commits

Jotted 2026-09-03.

It would be awesome to fold a whole commit block down to its `commit <sha>`
line (or its subject) when rewording a long range.

## Routes

- **Tree-sitter, editor side.** Zed folds on indentation and on outline
  items, so the existing `outline.scm` (`@item` = whole commit) may already
  make commits foldable. Verify in Zed before doing anything else. The
  message lines are indented so indent folding gives "fold the message
  under the commit line" too.
- **LSP `textDocument/foldingRange`.** One range per commit block from
  `ParseResult.blocks` (`line`..`end_line`), kind `region`, plus one per
  paragraph if useful. Trivial to implement; Zed needs
  `"document_folding_ranges": "on"` for the `Reword` language to use it.
  Portable to other editors.

Do both if the tree-sitter route turns out to need an `indents.scm` or
similar: the LSP one is a few lines either way.
