from __future__ import annotations
import re
import sys

_COLOR = sys.stdout.isatty()


def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def _red(t: str) -> str:    return _c(t, "38;2;220;50;47")
def _yellow(t: str) -> str: return _c(t, "33")
def _green(t: str) -> str:  return _c(t, "32")
def _bold(t: str) -> str:   return _c(t, "1")
def _dim(t: str) -> str:    return _c(t, "2")
def _orange(t: str) -> str: return _c(t, "38;5;208")


def _render_wecom_md(text: str) -> str:
    """
    Render the WeCom Markdown subset used by send_alerts() to ANSI terminal
    output so --dry-run shows an approximation of how the message will look.

    Handles: ## headings, **bold**, > blockquote,
             - bullets, <font color=…>, <@all>.
    """
    def _inline(s: str) -> str:
        # <font color="warning|info|comment">…</font>
        s = re.sub(r'<font color="warning">(.*?)</font>',
                   lambda m: _orange(m.group(1)), s)
        s = re.sub(r'<font color="info">(.*?)</font>',
                   lambda m: _green(m.group(1)), s)
        s = re.sub(r'<font color="comment">(.*?)</font>',
                   lambda m: _dim(m.group(1)), s)
        # **bold**
        s = re.sub(r'\*\*(.*?)\*\*', lambda m: _bold(m.group(1)), s)
        # <@all>
        s = s.replace("<@all>", _yellow("@all"))
        return s

    rendered = []
    for line in text.splitlines():
        if line.startswith("## "):
            rendered.append(_bold(line[3:]))
        elif line.startswith("# "):
            rendered.append(_bold(line[2:]))
        elif line.startswith("> "):
            rendered.append(_dim("▎ ") + _inline(line[2:]))
        elif line.startswith("- "):
            rendered.append("  • " + _inline(line[2:]))
        else:
            rendered.append(_inline(line))
    return "\n".join(rendered)
