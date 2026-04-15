"""Tests for atomic_write_text."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from thermal_monitor.io_utils import atomic_write_text


class TestAtomicWriteText:
    def test_creates_file(self, tmp_path):
        p = tmp_path / "out.json"
        atomic_write_text(str(p), "hello")
        assert p.read_text() == "hello"

    def test_overwrites_existing_file(self, tmp_path):
        p = tmp_path / "out.json"
        p.write_text("old")
        atomic_write_text(str(p), "new")
        assert p.read_text() == "new"

    def test_newline_eof_appends_newline(self, tmp_path):
        p = tmp_path / "out.json"
        atomic_write_text(str(p), "data", newline_eof=True)
        assert p.read_text() == "data\n"

    def test_no_trailing_newline_by_default(self, tmp_path):
        p = tmp_path / "out.json"
        atomic_write_text(str(p), "data")
        assert p.read_text() == "data"

    def test_creates_parent_directory(self, tmp_path):
        p = tmp_path / "nested" / "deep" / "out.json"
        atomic_write_text(str(p), "hi")
        assert p.read_text() == "hi"

    def test_leaves_old_content_if_write_fails(self, tmp_path):
        """A failure in os.replace must not destroy existing content."""
        p = tmp_path / "out.json"
        p.write_text("original")
        with patch("thermal_monitor.io_utils.os.replace",
                   side_effect=OSError("simulated")):
            with pytest.raises(OSError, match="simulated"):
                atomic_write_text(str(p), "new content")
        # Old file still intact; no partial-write visible.
        assert p.read_text() == "original"

    def test_cleans_up_tempfile_on_failure(self, tmp_path):
        """A raised error during replace must not litter the directory."""
        p = tmp_path / "out.json"
        p.write_text("x")
        with patch("thermal_monitor.io_utils.os.replace",
                   side_effect=OSError("boom")):
            with pytest.raises(OSError):
                atomic_write_text(str(p), "y")
        # Directory should only contain the original file — no *.tmp leftovers.
        leftovers = [f.name for f in tmp_path.iterdir()
                     if f.name != "out.json"]
        assert leftovers == []

    def test_new_file_honors_umask(self, tmp_path):
        """First-time writes should produce 0666 & ~umask, not mkstemp's 0600."""
        p = tmp_path / "fresh.json"
        old = os.umask(0o022)
        try:
            atomic_write_text(str(p), "x")
        finally:
            os.umask(old)
        assert (p.stat().st_mode & 0o777) == 0o644

    def test_new_file_honors_restrictive_umask(self, tmp_path):
        """A tight umask should still be respected, not widened."""
        p = tmp_path / "fresh.json"
        old = os.umask(0o077)
        try:
            atomic_write_text(str(p), "x")
        finally:
            os.umask(old)
        assert (p.stat().st_mode & 0o777) == 0o600

    def test_overwrite_preserves_existing_mode(self, tmp_path):
        """Replacing a file must keep its mode, regardless of the current umask."""
        p = tmp_path / "exists.json"
        p.write_text("old")
        os.chmod(p, 0o640)
        old = os.umask(0o077)    # a umask that would otherwise force 0600
        try:
            atomic_write_text(str(p), "new")
        finally:
            os.umask(old)
        assert (p.stat().st_mode & 0o777) == 0o640
        assert p.read_text() == "new"

    def test_no_partial_write_visible_mid_operation(self, tmp_path):
        """
        While atomic_write_text is between the tempfile write and os.replace,
        a reader of the target path must see the old file — never a partial
        one.  We verify by patching os.replace to inspect state at that point.
        """
        p = tmp_path / "out.json"
        p.write_text("ORIGINAL")
        snapshots = []

        real_replace = os.replace

        def spy_replace(src, dst):
            # At this moment the tempfile holds the new content but the dst
            # still has the old content — the guarantee under test.
            snapshots.append(p.read_text())
            real_replace(src, dst)

        with patch("thermal_monitor.io_utils.os.replace", side_effect=spy_replace):
            atomic_write_text(str(p), "REPLACEMENT")

        assert snapshots == ["ORIGINAL"]
        assert p.read_text() == "REPLACEMENT"
