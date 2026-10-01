# Plan: colour shas in messages through semantic tokens

Spec: `lsp-code-actions.md`, "Shas in messages", from commit 291aaf4.
Follows `sha-references.md`; same branch, `sha-references`.

## `analysis.py`

- `SEMANTIC_LEGEND = lsp.SemanticTokensLegend(token_types=["variable"],
  token_modifiers=["constant"])`.
- `Analysis.semantic_tokens() -> lsp.SemanticTokens`: one token per entry
  of `message_shas`, type 0, modifier bit 1, encoded relative to the
  previous token as LSP wants (delta line; delta start on the same line,
  absolute start on a new one). `message_shas` is already in document
  order. Columns are code points, as for the links and hovers.

## `server.py`

`textDocument/semanticTokens/full`, registered with
`lsp.SemanticTokensRegistrationOptions(legend=SEMANTIC_LEGEND, full=True)`.

## README

The language server list gets "shas in messages: link, hover, hint and
colour (Zed needs `"semantic_tokens": "combined"` for the language)".

## Tests

- `semantic_tokens()` on the file from `_with_sha_lines`: two tokens on
  the same line, `[line, 8, 7, 0, 1, 0, 12, 40, 0, 1]` in data, nothing for
  "defaced" or the comment line.
- The stdio server test advertises `semanticTokensProvider` with the
  legend, if it checks capabilities; otherwise a call through it.

One commit.
