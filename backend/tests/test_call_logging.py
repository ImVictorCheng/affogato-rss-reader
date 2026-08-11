from __future__ import annotations

import logging
import os
import stat

from backend.app import call_logging
from backend.app.call_logging import write_call_log


def test_safe_write_call_log_reports_filesystem_errors(monkeypatch, caplog):
    def fail_write(**_kwargs):
        raise OSError("read-only log directory")

    monkeypatch.setattr(call_logging, "write_call_log", fail_write)

    with caplog.at_level(logging.WARNING, logger=call_logging.__name__):
        assert call_logging.safe_write_call_log() is None

    assert "Unable to write the LLM/translation call log" in caplog.text
    assert "read-only log directory" in caplog.text


def test_call_log_rotation_obeys_configured_size_and_backup_count(settings):
    configured = settings.model_copy(
        update={"call_log_max_bytes": 1, "call_log_backups": 2}
    )
    for _ in range(4):
        write_call_log(
            category="llm",
            operation="test",
            status="success",
            duration_ms=1,
            settings=configured,
        )

    path = configured.effective_call_log_file
    assert path.is_file()
    assert path.with_name(f"{path.name}.1").is_file()
    assert path.with_name(f"{path.name}.2").is_file()
    assert not path.with_name(f"{path.name}.3").exists()
    if os.name != "nt":
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.with_name(f"{path.name}.1").stat().st_mode) == 0o600
        assert stat.S_IMODE(path.with_name(f"{path.name}.2").stat().st_mode) == 0o600
