# tree-sitter-reword

Tree-sitter grammar for the git-reword edit file. Format spec:
`../prose/spec/reword-format.md`.

`src/parser.c` is generated and committed so editors can build the grammar
without node. Regenerate after touching `grammar.js`:

```
../scripts/tree-sitter.sh generate
../scripts/tree-sitter.sh test
```

Queries in `queries/` use Zed's capture names. The Zed extension copies them.
