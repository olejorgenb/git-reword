"""Open a URL from the server process, for editors without window/showDocument."""

from __future__ import annotations

import subprocess
import sys


def opener(url: str, platform: str = sys.platform) -> list[str]:
    """Command that opens `url`: the zed CLI for zed:// (it routes into the
    running Zed), otherwise the desktop opener."""
    if url.startswith("zed://"):
        return ["zed", url]
    if platform == "darwin":
        return ["open", url]
    return ["xdg-open", url]


def open_url(url: str) -> str | None:
    """Spawn the opener detached. Returns an error message on failure."""
    cmd = opener(url)
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        return f"could not run {cmd[0]}: {e}"
    return None
