# Zed extension for git-reword

Language "Reword" for `.git/REWORD_EDITMSG` and `*.reword` files:
tree-sitter highlighting and outline, plus the `git-reword-lsp` language
server (diagnostics, hover, code actions, links, formatting).

## Install (dev extension)

Requirements: Rust with the `wasm32-wasip2` target (Zed adds it when Rust
comes from rustup), and `git-reword-lsp` on PATH. From the project root:

```
uv tool install --editable .        # puts git-reword and git-reword-lsp on PATH
```

Then in Zed: command palette, `zed: install dev extension`, pick this
directory (`zed-reword`).

If the server is not on the PATH Zed sees, point at it in settings:

```json
"lsp": {
  "git-reword-lsp": { "binary": { "path": "/home/ole/src/git-reword/.venv/bin/git-reword-lsp" } }
}
```

## Updating the grammar

`extension.toml` pins the grammar to a commit of this repository via a
`file://` URL. After changing `tree-sitter-reword/`, commit, update `rev`,
copy the queries:

```
cp tree-sitter-reword/queries/*.scm zed-reword/languages/reword/
```

and reinstall the dev extension.

## Try it

```
git reword --editor 'zed --wait' HEAD~3..HEAD
```
