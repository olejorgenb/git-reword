"""pygls wiring for the reword language server. Logic lives in analysis.py."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from lsprotocol import types as lsp
from pygls.lsp.server import LanguageServer

from git_reword.lsp.analysis import ADD_STATS, OPEN_COMMIT_COMMAND, Analysis, Repo
from git_reword.lsp.open import open_url

log = logging.getLogger("git-reword-lsp")


class RewordServer(LanguageServer):
    def __init__(self) -> None:
        super().__init__("git-reword-lsp", "0.1.0")
        self.repos: dict[Path, Repo | None] = {}
        self.client: str | None = None  # clientInfo.name from initialize

    def analysis(self, uri: str) -> Analysis:
        doc = self.workspace.get_text_document(uri)
        path = Path(doc.path)
        if path.parent not in self.repos:
            self.repos[path.parent] = Repo.discover(path)
        return Analysis(uri, doc.source, self.repos[path.parent], client=self.client)

    def supports_show_document(self) -> bool:
        window = self.client_capabilities.window
        return bool(window and window.show_document and window.show_document.support)

    def resolves_edits(self) -> bool:
        """Whether the client fills in a code action's `edit` through
        codeAction/resolve, so it can be left out of the first answer."""
        text_document = self.client_capabilities.text_document
        code_action = text_document.code_action if text_document else None
        support = code_action.resolve_support if code_action else None
        return support is not None and "edit" in support.properties

    def publish(self, uri: str) -> None:
        self.text_document_publish_diagnostics(
            lsp.PublishDiagnosticsParams(uri=uri, diagnostics=self.analysis(uri).diagnostics())
        )


server = RewordServer()


@server.feature(lsp.INITIALIZE)
def initialize(ls: RewordServer, params: lsp.InitializeParams) -> None:
    ls.client = params.client_info.name if params.client_info else None
    log.info(
        "initialize: client=%r show_document=%s resolve_edit=%s",
        ls.client,
        ls.supports_show_document(),
        ls.resolves_edits(),
    )


@server.feature(lsp.TEXT_DOCUMENT_DID_OPEN)
def did_open(ls: RewordServer, params: lsp.DidOpenTextDocumentParams) -> None:
    ls.publish(params.text_document.uri)


@server.feature(lsp.TEXT_DOCUMENT_DID_CHANGE)
def did_change(ls: RewordServer, params: lsp.DidChangeTextDocumentParams) -> None:
    ls.publish(params.text_document.uri)


@server.feature(lsp.TEXT_DOCUMENT_DID_SAVE)
def did_save(ls: RewordServer, params: lsp.DidSaveTextDocumentParams) -> None:
    ls.publish(params.text_document.uri)


@server.feature(lsp.TEXT_DOCUMENT_DID_CLOSE)
def did_close(ls: RewordServer, params: lsp.DidCloseTextDocumentParams) -> None:
    ls.text_document_publish_diagnostics(
        lsp.PublishDiagnosticsParams(uri=params.text_document.uri, diagnostics=[])
    )


@server.feature(lsp.TEXT_DOCUMENT_HOVER)
def hover(ls: RewordServer, params: lsp.HoverParams) -> lsp.Hover | None:
    return ls.analysis(params.text_document.uri).hover(params.position)


@server.feature(lsp.TEXT_DOCUMENT_DOCUMENT_SYMBOL)
def document_symbol(ls: RewordServer, params: lsp.DocumentSymbolParams) -> list[lsp.DocumentSymbol]:
    return ls.analysis(params.text_document.uri).symbols()


@server.feature(
    lsp.TEXT_DOCUMENT_CODE_ACTION,
    lsp.CodeActionOptions(
        code_action_kinds=[lsp.CodeActionKind.QuickFix, lsp.CodeActionKind.RefactorRewrite],
        resolve_provider=True,
    ),
)
def code_action(ls: RewordServer, params: lsp.CodeActionParams) -> list[lsp.CodeAction]:
    analysis = ls.analysis(params.text_document.uri)
    actions = analysis.code_actions(params.range, lazy=ls.resolves_edits())
    log.info("codeAction line %d: %s", params.range.start.line, [a.title for a in actions])
    return actions


@server.feature(lsp.CODE_ACTION_RESOLVE)
def code_action_resolve(ls: RewordServer, action: lsp.CodeAction) -> lsp.CodeAction:
    """Fill in a lazy edit, from the document as it is now."""
    data = action.data if isinstance(action.data, dict) else {}
    if data.get("action") == ADD_STATS and action.edit is None:
        uri = data["uri"]
        edits = ls.analysis(uri).stat_edits(data["shas"])
        action.edit = lsp.WorkspaceEdit(changes={uri: edits})
        log.info("codeAction/resolve %r: %d edits", action.title, len(edits))
    return action


@server.feature(lsp.TEXT_DOCUMENT_DOCUMENT_LINK)
def document_link(ls: RewordServer, params: lsp.DocumentLinkParams) -> list[lsp.DocumentLink]:
    links = ls.analysis(params.text_document.uri).links()
    log.info("documentLink: %d links", len(links))
    return links


@server.feature(lsp.TEXT_DOCUMENT_FOLDING_RANGE)
def folding_range(ls: RewordServer, params: lsp.FoldingRangeParams) -> list[lsp.FoldingRange]:
    ranges = ls.analysis(params.text_document.uri).folding_ranges()
    log.info(
        "foldingRange: %s",
        [(r.start_line, r.end_line, r.collapsed_text) for r in ranges],
    )
    return ranges


@server.feature(lsp.TEXT_DOCUMENT_FORMATTING)
def formatting(ls: RewordServer, params: lsp.DocumentFormattingParams) -> list[lsp.TextEdit]:
    return ls.analysis(params.text_document.uri).format_edits()


@server.command(OPEN_COMMIT_COMMAND)
def open_commit(ls: RewordServer, url: str) -> None:
    """Open a forge or zed:// URL. Zed lacks window/showDocument, so fall
    back to opening from this process. pygls maps each command argument to
    one parameter, hence `url` rather than an argument list."""
    if ls.supports_show_document():
        ls.window_show_document(lsp.ShowDocumentParams(uri=url, external=True))
    elif error := open_url(url):
        ls.window_show_message(lsp.ShowMessageParams(lsp.MessageType.Warning, error))


def main() -> None:
    # stderr is what editors show as "server logs" (Zed: the language server
    # log panel). One line per request that is otherwise invisible from the
    # editor side, so "is Zed asking?" can be answered after the fact.
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(name)s %(message)s"
    )
    logging.getLogger("pygls").setLevel(logging.WARNING)  # it echoes every response at INFO
    server.start_io()


if __name__ == "__main__":
    main()
