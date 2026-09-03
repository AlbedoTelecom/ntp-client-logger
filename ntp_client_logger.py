#!/usr/bin/env python3
"""Entry-point shim — logic lives in the ``ntp_logger`` package.

Kept so the invocation documented for cron / Task Scheduler still works:

    python3 ntp_client_logger.py --config config.yaml [--dry-run | --raw]

Equivalent to ``python3 -m ntp_logger``.
"""

from ntp_logger.cli import main

if __name__ == "__main__":
    main()
