"""Command-line entry point: wires config -> SSH -> parse -> CSV together.

Usage:
    python3 -m ntp_logger --config config.yaml
    python3 -m ntp_logger --config config.yaml --dry-run   (print parsed rows, don't write CSV)
    python3 -m ntp_logger --config config.yaml --raw       (print raw SSH output, for building the parser)
"""

import argparse
import logging
import os
import sys
import time

import paramiko

from ntp_logger.config import load_config
from ntp_logger.formatting import system_info_comment_lines
from ntp_logger.output import preview_register, write_register
from ntp_logger.parsing import looks_like_client_list, parse_client_list, parse_system_info
from ntp_logger.register import RegisterSchemaError
from ntp_logger.ssh_session import SSHConnectionError, run_session_with_retry

__all__ = ["main", "setup_logging", "write_register"]


def _log_format(use_utc: bool) -> str:
    # the " UTC" marker only appears when timestamps are actually in UTC, so a
    # log line is never ambiguous about which zone it is in
    if use_utc:
        return "%(asctime)s UTC [%(levelname)s] %(message)s"
    return "%(asctime)s [%(levelname)s] %(message)s"


def setup_logging(cfg: dict) -> None:
    log_cfg = cfg["logging"]
    log_path = log_cfg["log_path"]
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)

    # logging.utc: true -> %(asctime)s renders in UTC (matches the register's
    # last_seen_utc column). Default false = the host's local time.
    use_utc = bool(log_cfg.get("utc", False))
    logging.Formatter.converter = time.gmtime if use_utc else time.localtime

    logging.basicConfig(
        level=level,
        format=_log_format(use_utc),
        handlers=[
            logging.FileHandler(log_path),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )


def main():
    parser = argparse.ArgumentParser(description="Log NTP appliance client list to CSV")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Parse and print rows, but don't write CSV")
    parser.add_argument("--raw", action="store_true", help="Print raw SSH output and exit (for building the parser)")
    args = parser.parse_args()

    cfg = load_config(args.config)
    setup_logging(cfg)

    logging.info("Connecting to %s", cfg["ssh"]["host"])
    try:
        session = run_session_with_retry(cfg)
    except paramiko.AuthenticationException as e:
        logging.error("SSH authentication failed (check username / password / key): %s", e)
        sys.exit(1)
    except SSHConnectionError as e:
        logging.error("%s", e)
        sys.exit(1)
    except OSError as e:
        # transient socket errors are wrapped in SSHConnectionError by the retry
        # layer, so a raw OSError here means a broken private_key_path
        logging.error("SSH key path unusable (check ssh.private_key_path): %s", e)
        sys.exit(1)
    except Exception as e:
        logging.error("SSH session failed: %s", e)
        sys.exit(1)

    device_info_raw = session["device_info"]
    outputs_by_interface = session["interfaces"]

    if args.raw:
        if device_info_raw is not None:
            print("----- RAW OUTPUT: device_info_command -----")
            print(device_info_raw)
            print("----- END: device_info_command -----\n")
        for interface, raw_output in outputs_by_interface.items():
            print(f"----- RAW OUTPUT: {interface} -----")
            print(raw_output)
            print(f"----- END: {interface} -----\n")
        sys.exit(0)

    device_info = parse_system_info(device_info_raw) if device_info_raw else {}
    if device_info:
        summary = "; ".join(
            f"{section}: " + ", ".join(f"{k}={v}" for k, v in kvs.items())
            for section, kvs in device_info.items()
        )
        logging.info("Device info — %s", summary)
        if args.dry_run:
            for line in system_info_comment_lines(device_info, "<capture timestamp>"):
                print(line)
    elif device_info_raw:
        logging.warning(
            "device_info_command ran but produced no parseable key/value lines "
            "— check --raw output; no device-info header will be written"
        )

    exit_code = 0
    for interface, raw_output in outputs_by_interface.items():
        if not looks_like_client_list(raw_output):
            said = " | ".join(raw_output.strip().splitlines()[:3]) or "<no output>"
            logging.error(
                "[%s] appliance did not return an NTP client list — the interface "
                "name is probably wrong. Appliance said: %s", interface, said,
            )
            exit_code = 1
            continue

        rows = parse_client_list(raw_output)
        logging.info("[%s] Parsed %d client rows", interface, len(rows))

        if not rows:
            logging.warning("[%s] No client rows parsed — check parser against --raw output", interface)

        bad_pct = [
            r["address"] for r in rows
            if not str(r.get("percent", "")).strip().lstrip("+-").isdigit()
        ]
        if bad_pct:
            logging.warning(
                "[%s] non-numeric %% for %d client(s), counted as 0 in avg/peak: %s",
                interface, len(bad_pct), ", ".join(bad_pct),
            )

        if args.dry_run:
            print(f"--- {interface} (register preview, not written) ---")
            try:
                print(preview_register(rows, interface, cfg).rstrip())
            except RegisterSchemaError as e:
                logging.error("[%s] register file unreadable: %s", interface, e)
                exit_code = 1
            continue

        try:
            stats = write_register(rows, interface, cfg, device_info=device_info)
        except RegisterSchemaError as e:
            logging.error("[%s] register file unreadable: %s", interface, e)
            exit_code = 1
            continue
        logging.info(
            "[%s] Register: %d IP(s) total, %d seen this poll, %d new — %s",
            interface, stats["total"], stats["seen_this_poll"], stats["new"], stats["path"],
        )

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
