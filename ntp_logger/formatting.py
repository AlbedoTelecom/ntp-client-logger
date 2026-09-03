"""Rendering helpers (no I/O, no third-party deps).

Kept separate from ``parsing`` so the CSV comment-header format has one home and
can be unit-tested on its own.
"""

__all__ = ["system_info_comment_lines"]


def system_info_comment_lines(device_info: dict, captured_utc: str) -> list:
    """Render ``parse_system_info`` output as ``#``-prefixed CSV comment lines.

    ``device_info`` is the ordered ``{section: {label: value}}`` dict. The lines
    are returned without trailing newlines; the caller joins them. Returns an
    empty list when there is nothing to render.
    """
    if not device_info:
        return []

    lines = [f"# ntp client log — device info captured (UTC {captured_utc})"]
    for section, kvs in device_info.items():
        lines.append(f"# [{section or 'info'}]")
        for label, value in kvs.items():
            lines.append(f"#   {label}: {value}")
    return lines
