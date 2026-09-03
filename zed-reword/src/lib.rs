//! Zed extension for git-reword edit files.
//!
//! Highlighting and outline come from the tree-sitter grammar declared in
//! extension.toml. This shim only tells Zed how to start the language
//! server: `git-reword-lsp` from PATH, or the binary configured in settings:
//!
//! ```json
//! "lsp": { "git-reword-lsp": { "binary": { "path": "/path/to/git-reword-lsp" } } }
//! ```

use zed_extension_api::{self as zed, settings::LspSettings, LanguageServerId, Result};

const SERVER: &str = "git-reword-lsp";

struct RewordExtension;

impl zed::Extension for RewordExtension {
    fn new() -> Self {
        Self
    }

    fn language_server_command(
        &mut self,
        _language_server_id: &LanguageServerId,
        worktree: &zed::Worktree,
    ) -> Result<zed::Command> {
        let binary = LspSettings::for_worktree(SERVER, worktree)
            .ok()
            .and_then(|settings| settings.binary);

        let command = binary
            .as_ref()
            .and_then(|b| b.path.clone())
            .or_else(|| worktree.which(SERVER))
            .ok_or_else(|| {
                format!(
                    "{SERVER} not found on PATH. Install git-reword so the script is on PATH, \
                     or set lsp.{SERVER}.binary.path in Zed settings."
                )
            })?;

        Ok(zed::Command {
            command,
            args: binary.and_then(|b| b.arguments).unwrap_or_default(),
            env: Default::default(),
        })
    }
}

zed::register_extension!(RewordExtension);
