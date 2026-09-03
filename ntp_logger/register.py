"""The client register: one row per IP, tallying how often it has been seen and
its share of the appliance's NTP query load.

Pure functions (text in, data out) so the read-modify-write cycle is unit-tested
without touching the filesystem. ``ntp_logger.output.write_register`` does the I/O.

Register row schema (CSV):

    interface,address,times_seen,polls_observed,last_seen_utc,elapsed_seconds,avg_percent,peak_percent

- ``times_seen``      — polls in which this IP appeared (+1 per poll it's in)
- ``polls_observed``  — polls since this IP was first seen (+1 every poll,
                        present or not). ``times_seen / polls_observed`` is how
                        often the client is actually here.
- ``last_seen_utc``   — UTC timestamp of the most recent poll it appeared in
- ``elapsed_seconds`` — value from that most recent poll (frozen at last sighting)
- ``avg_percent``     — cumulative mean of the appliance's ``%`` column over
                        ``polls_observed`` polls, counting a poll the IP was
                        **absent** from as ``0``. So it's the client's average
                        share of load since it was first seen — comparable
                        across rows. A client that leaves decays toward 0.
- ``peak_percent``    — the highest ``%`` ever seen for this IP (only a real
                        sighting can raise it; an absent poll never does)

Row order is preserved: existing rows first, then new IPs in the order the
appliance listed them.
"""

import csv
import io

__all__ = ["REGISTER_FIELDS", "RegisterSchemaError", "load_register", "update_register", "format_register_csv"]

REGISTER_FIELDS = [
    "interface", "address", "times_seen", "polls_observed", "last_seen_utc",
    "elapsed_seconds", "avg_percent", "peak_percent",
]


class RegisterSchemaError(Exception):
    """The file on disk is not a register in the current schema — an old
    append-style snapshot CSV, a register from an earlier schema version, or a
    row with a non-numeric value."""


def _as_int(value, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def load_register(text: str) -> dict:
    """Parse register CSV ``text`` into ``{address: {...}}`` (ordered). ``#``
    comment lines are ignored. Empty / header-only text yields ``{}``. Raises
    :class:`RegisterSchemaError` on a wrong column set or a non-numeric numeric
    field.
    """
    data_lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    if not data_lines:
        return {}

    reader = csv.DictReader(data_lines)
    if reader.fieldnames is None:
        return {}
    if list(reader.fieldnames) != REGISTER_FIELDS:
        raise RegisterSchemaError(
            f"unexpected columns {list(reader.fieldnames)}; expected {REGISTER_FIELDS}. "
            "If this is a register from an older version or an old snapshot-format "
            "CSV, move it aside and rerun."
        )

    register = {}
    for line_no, row in enumerate(reader, start=2):
        address = (row.get("address") or "").strip()
        if not address:
            continue
        try:
            times_seen = int(row["times_seen"])
            polls_observed = int(row["polls_observed"])
            elapsed_seconds = int(row["elapsed_seconds"])
            avg_percent = float(row["avg_percent"])
            peak_percent = int(row["peak_percent"])
        except (TypeError, ValueError) as e:
            raise RegisterSchemaError(f"row {line_no}: non-numeric value ({e})") from e

        register[address] = {
            "interface": (row.get("interface") or "").strip(),
            "times_seen": times_seen,
            "polls_observed": polls_observed,
            "last_seen_utc": (row.get("last_seen_utc") or "").strip(),
            "elapsed_seconds": elapsed_seconds,
            "avg_percent": avg_percent,
            "peak_percent": peak_percent,
        }
    return register


def update_register(register: dict, poll_rows: list, *, interface: str, now_iso: str) -> dict:
    """Return a new register with this poll folded in. The input is not mutated.

    Every existing row advances one poll: ``polls_observed`` +1, and ``%`` is
    folded into ``avg_percent`` — the row's real ``%`` if the IP is in
    ``poll_rows``, otherwise ``0``. Rows for IPs in this poll additionally bump
    ``times_seen`` and refresh ``last_seen_utc`` / ``elapsed_seconds`` /
    ``peak_percent``. IPs seen for the first time are appended. ``poll_rows`` are
    dicts from :func:`ntp_logger.parsing.parse_client_list`; a non-numeric
    ``percent`` counts as 0.
    """
    updated = {addr: dict(entry) for addr, entry in register.items()}
    poll_by_addr = {row["address"]: row for row in poll_rows}

    # 1. advance every existing row by one poll (absent IPs contribute % = 0)
    for address, entry in updated.items():
        entry["polls_observed"] += 1
        row = poll_by_addr.get(address)
        if row is not None:
            pct = _as_int(row.get("percent"), 0)
            entry["interface"] = interface
            entry["times_seen"] += 1
            entry["last_seen_utc"] = now_iso
            entry["elapsed_seconds"] = row["elapsed_seconds"]
            entry["peak_percent"] = max(entry["peak_percent"], pct)
        else:
            pct = 0
        entry["avg_percent"] = round(
            entry["avg_percent"] + (pct - entry["avg_percent"]) / entry["polls_observed"], 2
        )

    # 2. append IPs seen for the first time this poll
    for row in poll_rows:
        address = row["address"]
        if address in updated:
            continue
        pct = _as_int(row.get("percent"), 0)
        updated[address] = {
            "interface": interface,
            "times_seen": 1,
            "polls_observed": 1,
            "last_seen_utc": now_iso,
            "elapsed_seconds": row["elapsed_seconds"],
            "avg_percent": float(pct),
            "peak_percent": pct,
        }

    return updated


def format_register_csv(register: dict) -> str:
    """Render a register dict back to CSV text (header + one row per IP)."""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=REGISTER_FIELDS, lineterminator="\n")
    writer.writeheader()
    for address, entry in register.items():
        writer.writerow({
            "interface": entry["interface"],
            "address": address,
            "times_seen": entry["times_seen"],
            "polls_observed": entry["polls_observed"],
            "last_seen_utc": entry["last_seen_utc"],
            "elapsed_seconds": entry["elapsed_seconds"],
            "avg_percent": f"{entry['avg_percent']:.2f}",
            "peak_percent": entry["peak_percent"],
        })
    return out.getvalue()
