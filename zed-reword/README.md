# Zed extension for git-reword

Language "Reword" for `REWORD_EDITMSG` (written at the worktree root by
`git reword`) and `*.reword` files:
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

## Code actions

"Open `<sha>` in Zed" opens Zed's commit view (message, diff, open-on-remote
button) through `zed://git/commit/<sha>?repo=<worktree root>`. Zed has no
`window/showDocument`, so the server runs the `zed` CLI itself; for a dev
build that is the `zed` the fork's install script puts on PATH, and the URL
lands in the running instance. "Open `<sha>` in browser" goes through
`xdg-open`.

"Reflow paragraph" does what `editor: rewrap` does, but knows to leave the
subject and trailers alone.

Each sha is also a document link: ctrl-click opens the commit in Zed's
commit view. Zed passes `zed://` links to the desktop's default handler, so
that handler must be the Zed you are running.

## Recommended settings

Language `config.toml` cannot set these, so add them to `settings.json`:

```json
"languages": {
  "Reword": {
    "preferred_line_length": 76,
    "wrap_guides": [76],
    "allow_rewrap": "anywhere",
    "document_folding_ranges": "on"
  }
}
```

Message lines are indented by 4, and both rewrap and the wrap guide count
the indent, so 76 gives the usual 72 columns of text. `allow_rewrap` is
needed because Zed's default (`in_comments`) makes `editor: rewrap` a no-op
outside `#` comments. Rewrap stops at blank lines and indent changes, so it
never joins the subject with the body.

`document_folding_ranges` makes Zed fold with the server's ranges instead
of by indentation, which would hide the subject along with the body. With
the cursor in a body, `editor: fold` hides just the body; on the `commit`
or info lines it collapses the whole block to the `commit` line with the
subject shown as the fold placeholder.

If the settings do not take effect, check the language name in the status
bar: it must say `Reword`, not `Plain Text`.

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
