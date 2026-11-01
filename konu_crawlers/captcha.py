"""Human-in-the-loop captcha entry.

Some government portals (TS-RERA search, Bhu Bharati, IGRS) put their public
search behind an image captcha. These crawlers do **not** try to solve
captchas automatically; instead the image is saved to disk and the operator
types the answer. Supply answers non-interactively with ``--captcha`` only if
you have read the image yourself.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from .http import CaptchaRequired


def ask_human(image: bytes, source: str, suffix: str = ".png", provided: str | None = None) -> str:
    if provided:
        return provided
    path = Path(tempfile.gettempdir()) / f"konu-{source}-captcha{suffix}"
    path.write_bytes(image)
    if not sys.stdin.isatty() or os.environ.get("KONU_NONINTERACTIVE"):
        raise CaptchaRequired(
            f"{source}: this search requires a captcha. The image was saved to {path}; "
            "run the crawler in an interactive terminal (or pass --captcha TEXT after reading it)."
        )
    print(f"\n[{source}] Captcha image saved to: {path}", file=sys.stderr)
    _try_show_inline(path)
    return input(f"[{source}] Type the captcha text: ").strip()


def _try_show_inline(path: Path) -> None:
    """Best effort: open the image with the desktop viewer if there is one."""
    if os.environ.get("DISPLAY") or sys.platform in ("darwin", "win32"):
        try:
            import webbrowser

            webbrowser.open(path.as_uri())
        except Exception:  # noqa: BLE001
            pass
