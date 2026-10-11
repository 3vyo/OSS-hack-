"""Desktop clipboard access.

Textual copies with an OSC 52 escape code, which VTE terminals such as GNOME Terminal ignore,
and its ctrl+v pastes from Textual's own clipboard. These helpers use the desktop's tools instead.
"""

import os
import shutil
import subprocess


def _command(paste: bool) -> list[str] | None:
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
        return ["wl-paste", "--no-newline"] if paste else ["wl-copy"]
    if os.environ.get("DISPLAY"):
        if shutil.which("xclip"):
            return ["xclip", "-selection", "clipboard"] + (["-o"] if paste else [])
        if shutil.which("xsel"):
            return ["xsel", "--clipboard", "--output" if paste else "--input"]
    if shutil.which("pbcopy"):
        return ["pbpaste"] if paste else ["pbcopy"]
    return None


def copy(text: str) -> bool:
    command = _command(paste=False)
    if command is None:
        return False
    try:
        # No pipes on stdout/stderr: wl-copy leaves a background process holding them open.
        subprocess.run(command, input=text.encode(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=2, check=True)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def paste() -> str | None:
    command = _command(paste=True)
    if command is None:
        return None
    try:
        result = subprocess.run(command, capture_output=True, timeout=2, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.decode("utf-8", errors="replace")
