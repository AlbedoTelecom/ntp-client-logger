"""SSH connection and interactive-shell command execution against the appliance.

The appliance CLI needs an interactive shell (``invoke_shell``), not
``exec_command`` — many of these firmwares present a session/menu rather than
running a one-shot command. Everything here is read-only (``show`` commands), so
retrying a whole session on a transient failure is safe.
"""

import logging
import time

import paramiko

__all__ = [
    "ssh_connect",
    "run_remote_session",
    "run_session_with_retry",
    "SSHConnectionError",
]

# Exceptions worth retrying: network flakiness, dropped connections, a slow or
# missing SSH banner. ``OSError`` covers socket.timeout / TimeoutError /
# ConnectionReset / ConnectionRefused / paramiko's NoValidConnectionsError.
_TRANSIENT_ERRORS = (paramiko.SSHException, OSError, EOFError)

# ``OSError`` subclasses that mean a broken ``private_key_path`` (missing file,
# bad permissions, path is a directory). Retrying these is pointless — re-raise
# immediately instead of counting them as transient.
_FATAL_OS_ERRORS = (FileNotFoundError, PermissionError, IsADirectoryError, NotADirectoryError)


class SSHConnectionError(Exception):
    """Raised when an SSH session could not be completed after all retries."""


def _retry(operation, *, attempts: int, backoff: float, sleep=time.sleep):
    """Call ``operation()``; retry on transient SSH/socket errors.

    Re-raises non-transient errors immediately (auth failure, missing key file).
    After ``attempts`` transient failures, raises :class:`SSHConnectionError`
    chained to the last one. ``sleep`` is injectable for tests.
    """
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            result = operation()
            if attempt > 1:
                logging.info("SSH session succeeded on attempt %d/%d", attempt, attempts)
            return result
        except paramiko.AuthenticationException:
            raise  # wrong credentials will not fix themselves
        except _FATAL_OS_ERRORS:
            raise  # broken private_key_path
        except _TRANSIENT_ERRORS as e:
            last_exc = e
            logging.warning(
                "SSH attempt %d/%d failed: %s: %s",
                attempt, attempts, type(e).__name__, e,
            )
            if attempt < attempts:
                sleep(backoff * attempt)  # linear backoff: b, 2b, 3b, ...

    raise SSHConnectionError(
        f"could not complete SSH session after {attempts} attempt(s): {last_exc}"
    ) from last_exc


def ssh_connect(ssh_cfg: dict) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    connect_kwargs = {
        "hostname": ssh_cfg["host"],
        "port": ssh_cfg.get("port", 22),
        "username": ssh_cfg["username"],
        "timeout": ssh_cfg.get("connect_timeout", 10),
        "banner_timeout": ssh_cfg.get("banner_timeout", 15),
        "auth_timeout": ssh_cfg.get("auth_timeout", 15),
    }

    if ssh_cfg.get("auth_method") == "key":
        connect_kwargs["key_filename"] = ssh_cfg["private_key_path"]
    else:
        connect_kwargs["password"] = ssh_cfg.get("password", "")

    client.connect(**connect_kwargs)
    return client


def run_remote_session(client: paramiko.SSHClient, cfg: dict) -> dict:
    """Open one interactive shell, run any ``pre_commands`` (output discarded),
    then the optional ``device_info_command`` (output kept), then
    ``show ntp <interface> clients`` for every interface in ``cfg["interfaces"]``,
    capturing each command's output separately.

    Return::

        {
            "device_info": <raw text> or None,
            "interfaces": {interface_name: raw_output_text, ...},
        }
    """
    ssh_cfg = cfg.get("ssh", {})
    command_wait = float(ssh_cfg.get("command_wait_seconds", 2.0))

    shell = client.invoke_shell()
    shell.settimeout(max(command_wait * 4, 15))
    output_buffer = ""

    def drain() -> None:
        nonlocal output_buffer
        while shell.recv_ready():
            output_buffer += shell.recv(65535).decode(errors="ignore")

    def send(cmd: str, wait: float = None) -> str:
        """Send ``cmd``; return the text that arrived in response.

        Waits ``wait`` seconds for output to start, then keeps reading until the
        stream has been quiet for ~0.6s (a fixed single sleep truncates the
        table on a slow appliance), bounded by an 8s hard cap.
        """
        nonlocal output_buffer
        settle = command_wait if wait is None else wait
        marker_before = len(output_buffer)

        shell.send(cmd + "\n")
        time.sleep(settle)

        deadline = time.monotonic() + settle + 8
        quiet_since = None
        while True:
            before = len(output_buffer)
            drain()
            if len(output_buffer) > before:
                quiet_since = None
            elif quiet_since is None:
                quiet_since = time.monotonic()
            elif time.monotonic() - quiet_since >= 0.6:
                break
            if time.monotonic() > deadline:
                break
            time.sleep(0.2)

        return output_buffer[marker_before:]

    # Drain any login banner first
    time.sleep(1)
    drain()

    for pre_cmd in cfg.get("pre_commands", []):
        send(pre_cmd)

    device_info = None
    device_info_command = (cfg.get("device_info_command") or "").strip()
    if device_info_command:
        device_info = send(device_info_command)

    interfaces = {}
    for interface in cfg["interfaces"]:
        cmd = cfg["command_template"].format(interface=interface)
        interfaces[interface] = send(cmd)

    shell.close()
    return {"device_info": device_info, "interfaces": interfaces}


def run_session_with_retry(cfg: dict) -> dict:
    """Connect and run one :func:`run_remote_session`, retrying the whole unit on
    transient failures per ``cfg["ssh"]`` (``retries``, ``retry_backoff_seconds``).
    """
    ssh_cfg = cfg["ssh"]
    attempts = max(1, int(ssh_cfg.get("retries", 3)))
    backoff = float(ssh_cfg.get("retry_backoff_seconds", 5))

    def _once() -> dict:
        client = ssh_connect(ssh_cfg)
        try:
            return run_remote_session(client, cfg)
        finally:
            client.close()

    return _retry(_once, attempts=attempts, backoff=backoff)
