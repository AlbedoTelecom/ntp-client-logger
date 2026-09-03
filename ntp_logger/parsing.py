"""Pure parsing helpers for the appliance's ``show ntp <interface> clients`` output.

These functions have no I/O and no third-party dependencies, so they are the
part of the project that is unit-tested directly.
"""

import re

__all__ = [
    "parse_client_list",
    "elapsed_to_seconds",
    "parse_system_info",
    "looks_like_client_list",
]


def looks_like_client_list(raw_output: str) -> bool:
    """True if ``raw_output`` looks like a ``show ntp <iface> clients`` response.

    Positive-signal check: a valid interface's output always contains the phrase
    ``NTP clients list`` (the table header) or ``N NTP clients listed`` (the
    trailer, present even for an empty list) — ``"clients list"`` is a substring
    of both. A wrong interface name instead yields an appliance error such as
    ``Error: Unknown selection value 'bogus99'``, which matches neither.
    """
    return "clients list" in raw_output.lower()


def elapsed_to_seconds(elapsed: str) -> int:
    """Convert ``HH:MM:SS`` to total seconds. Return -1 if unparseable."""
    parts = elapsed.split(":")
    try:
        if len(parts) == 3:
            h, m, s = (int(p) for p in parts)
            return h * 3600 + m * 60 + s
    except ValueError:
        pass
    return -1


def parse_client_list(raw_output: str) -> list:
    """Parse the appliance's ``show ntp <interface> clients`` output, e.g.::

        NTP clients list ETH(P)
        Address                 Elapsed                  %
        --------------------------------------------------
        192.0.2.1              00:01:00                 94
        192.0.2.6              00:15:22                  1
        4 NTP clients listed

    Return a list of dicts: address, elapsed, elapsed_seconds, percent.
    """
    rows = []
    in_table = False

    for line in raw_output.splitlines():
        stripped = line.strip()

        if stripped.startswith("---"):
            in_table = True
            continue

        if not in_table:
            continue

        if not stripped or "NTP clients listed" in stripped:
            break  # end of table

        if stripped.lower().startswith("address"):
            continue  # header row, already past the dashes in some edge cases

        cols = stripped.split()
        if len(cols) < 3:
            continue

        address, elapsed, percent = cols[0], cols[1], cols[2]
        if not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", address):
            continue  # not a real client row, skip defensively

        rows.append({
            "address": address,
            "elapsed": elapsed,
            "elapsed_seconds": elapsed_to_seconds(elapsed),
            "percent": percent,
        })

    return rows


def parse_system_info(raw_output: str) -> dict:
    """Parse the appliance's ``show system`` output, e.g.::

        System information
        --------------------------------------------------
        Model name:                               Net.Time
        Serial number:                            EXAMPLE1
        ...

        Diagnostics information
        --------------------------------------------------
        PSU 1:                                    AC    OK
        ...

    Return an ordered ``{section_title: {label: value}}`` dict. A section is a
    colon-free line immediately followed by a ``---`` rule; ``label: value``
    lines are split on the first colon with the value's internal whitespace
    collapsed. The echoed command and trailing shell prompt (colon-free lines
    with no following rule) are ignored. ``label: value`` lines seen before any
    section land under the ``""`` key.
    """
    sections = {}
    current_section = None
    pending_title = None

    for line in raw_output.splitlines():
        stripped = line.strip()

        if not stripped:
            continue

        if stripped.startswith("---"):
            if pending_title is not None:
                current_section = pending_title
                sections.setdefault(current_section, {})
                pending_title = None
            continue

        if ":" in stripped:
            label, value = stripped.split(":", 1)
            label = label.strip()
            value = " ".join(value.split())
            if label:
                sections.setdefault(current_section or "", {})[label] = value
            continue

        # colon-free, non-rule line: a candidate section title, confirmed only
        # if the next non-blank line is a "---" rule.
        pending_title = stripped

    return sections
