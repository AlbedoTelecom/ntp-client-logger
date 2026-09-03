"""ntp_logger — log an NTP appliance's per-interface client list to CSV.

Only the dependency-free parsing helpers are re-exported here so that
``import ntp_logger`` (and the test suite) does not require paramiko.
"""

from ntp_logger.parsing import (
    elapsed_to_seconds,
    looks_like_client_list,
    parse_client_list,
    parse_system_info,
)

__all__ = [
    "parse_client_list",
    "elapsed_to_seconds",
    "parse_system_info",
    "looks_like_client_list",
]
__version__ = "0.1.0"
