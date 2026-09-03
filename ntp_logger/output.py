"""CSV output: one client register per interface, read-modify-written each poll,
with an optional device-info comment header (refreshed every run).

No third-party deps, so the writer is unit-tested directly.
"""

import os
from datetime import datetime, timezone

from ntp_logger.formatting import system_info_comment_lines
from ntp_logger.register import format_register_csv, load_register, update_register

__all__ = ["write_register", "preview_register"]


def _register_path(cfg: dict, interface: str) -> str:
    csv_dir = cfg["output"]["csv_dir"]
    filename = cfg["output"]["filename_pattern"].format(interface=interface)
    return os.path.join(csv_dir, filename)


def _merge(rows: list, interface: str, cfg: dict):
    """Read the existing register (if any) and fold this poll in — no writing.

    Raises ``ntp_logger.register.RegisterSchemaError`` if the file on disk is not
    in the current schema. Returns ``(path, merged_register, seen_before, now_iso)``.
    """
    path = _register_path(cfg, interface)

    existing_text = ""
    if os.path.exists(path):
        with open(path, "r", newline="") as f:
            existing_text = f.read()

    register = load_register(existing_text)
    seen_before = set(register)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    merged = update_register(register, rows, interface=interface, now_iso=now)
    return path, merged, seen_before, now


def write_register(rows: list, interface: str, cfg: dict, device_info: dict = None) -> dict:
    """Fold this poll's ``rows`` into the interface's register file and rewrite it.

    Bumps ``times_seen`` / ``last_seen_utc`` / ``elapsed_seconds`` for every IP in
    ``rows``, leaves other rows untouched. The file is written atomically (temp
    file + ``os.replace``) so a crash mid-write can't truncate the tally history.
    Raises ``RegisterSchemaError`` if the file on disk is not in the current
    schema. Returns ``{path, total, seen_this_poll, new}``.
    """
    csv_dir = cfg["output"]["csv_dir"]
    os.makedirs(csv_dir, exist_ok=True)

    path, merged, seen_before, now = _merge(rows, interface, cfg)

    body = format_register_csv(merged)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", newline="") as f:
            for line in system_info_comment_lines(device_info, now):
                f.write(line + "\n")
            f.write(body)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    return {
        "path": path,
        "total": len(merged),
        "seen_this_poll": len(rows),
        "new": len(set(merged) - seen_before),
    }


def preview_register(rows: list, interface: str, cfg: dict) -> str:
    """--dry-run helper: return the register CSV this poll *would* produce,
    without writing anything. Raises ``RegisterSchemaError`` exactly as
    :func:`write_register` would, so a stale-format file is caught here too.
    """
    _path, merged, _seen, _now = _merge(rows, interface, cfg)
    return format_register_csv(merged)
