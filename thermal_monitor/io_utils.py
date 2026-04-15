"""
Small I/O helpers shared across the package.

Keeping these in their own module avoids circular imports (e.g. alerts.py
and cli.py both need atomic writes but can't pull each other's module in).
"""
from __future__ import annotations
import os
import tempfile
from pathlib import Path


def atomic_write_text(path: str, text: str, *, newline_eof: bool = False) -> None:
    """
    Write *text* to *path* atomically.

    Writes to a sibling tempfile, fsyncs it, then os.replace()s it onto
    *path*.  A crash or SIGTERM mid-write leaves either the old file
    intact or the new file complete — never a half-written one.  This
    matters for:

    * The readings JSON served to the web dashboard (partial JSON breaks
      the browser's JSON.parse).
    * The alert cooldown state file (a zero-length file makes
      ``_load_state`` silently fall back to an empty dict, so WARN/CRIT
      alerts re-fire every cycle after a crash).

    If *newline_eof* is true, a trailing newline is appended before the
    replace — matches the previous ``write_text(s + "\\n")`` convention
    used for the JSON output.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # NamedTemporaryFile in the target's directory so os.replace is atomic
    # (same-filesystem rename).  delete=False + manual unlink on failure.
    fd, tmp = tempfile.mkstemp(
        prefix=p.name + ".", suffix=".tmp", dir=str(p.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            if newline_eof:
                fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        # mkstemp() always creates files 0600 (a security feature for
        # shared tempdirs).  For a user-visible atomic write we want the
        # final file to match whatever a plain open() would produce:
        # preserve the existing destination's mode when replacing a file,
        # or fall back to the umask-derived mode for first-time creation.
        # Without this, consumers that rely on umask/UMask= (e.g. a web
        # server reading readings.json) see 0600 files no matter what.
        try:
            mode = os.stat(p).st_mode & 0o777
        except FileNotFoundError:
            umask = os.umask(0)
            os.umask(umask)
            mode = 0o666 & ~umask
        os.chmod(tmp, mode)
        os.replace(tmp, p)
    except Exception:
        # Best-effort cleanup of the tempfile; re-raise the original error.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
