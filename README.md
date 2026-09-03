# NTP Client Logger

Polls an NTP appliance over SSH on a schedule and maintains a per-interface
**client register** — one CSV row per client IP, tracking how many polls it has
appeared in (`times_seen`), when it was last seen, and its share of the
appliance's NTP query load (`avg_percent` / `peak_percent`). Built and tested
against a Net.Time / SecureSync-style CLI (`show ntp <interface> clients`).

Requires Python 3.9+.

## 1. Install

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

All commands below call `./.venv/bin/python` directly, so they work from any
shell without activating the venv first.

## 2. Configure

Copy the template and edit it (`config.yaml` is gitignored — it holds credentials):

```bash
cp config.example.yaml config.yaml
```

- `ssh.host`, `ssh.username`, `ssh.private_key_path` (or password)
- `interfaces`: list the physical NTP ports active on this specific appliance
  (e.g. `ntp01`, `ntp01r`, up to 4). Each one gets its own command run and
  its own CSV file. This is the per-environment variable — different
  deployments will have different interface names, so it lives in config,
  not in the script.
- `output.csv_dir`: folder where the per-interface CSVs are written
- `device_info_command` (optional): a command run once per session whose output
  describes the appliance (default `show system`). Its parsed key/value output
  is written as a `#`-comment block at the top of each CSV when the file is
  first created. Leave blank to skip.

The command itself (`show ntp <interface> clients`) is already wired up via
`command_template` — you shouldn't need to touch that unless the syntax
differs on your firmware version.

## 3. Verify against your appliance (do this first!)

```bash
./.venv/bin/python ntp_client_logger.py --config config.yaml --raw
```

This prints the raw SSH output for `device_info_command` (if set) and for every
interface listed in `interfaces`. `--raw` is a dump only — it does not validate
interface names or exit non-zero on a bad one; use `--dry-run` for that.
Confirm it matches the expected table format (Address / Elapsed / % columns).
The included parser is already built and tested against this exact format:

```
NTP clients list ETH(P)
Address                 Elapsed                  %
--------------------------------------------------
192.0.2.1              00:01:00                 94
4 NTP clients listed
```

If your appliance's output differs at all (extra header lines, different
column order, etc.), adjust `parse_client_list()` in `ntp_logger/parsing.py`
accordingly.

Test parsing without writing to CSV:

```bash
./.venv/bin/python ntp_client_logger.py --config config.yaml --dry-run
```

`--dry-run` also validates the `interfaces` list: if the appliance doesn't
recognise a name it logs an `ERROR` (quoting the appliance's own reply) and the
command exits non-zero, so a typo is caught before the first scheduled run.

## 4. Run it for real

```bash
./.venv/bin/python ntp_client_logger.py --config config.yaml
```

Each interface has one **register** file (e.g. `ntp_clients_ntp01r.csv`) — one
row per client IP, not a growing log of snapshots. Every run reads the file,
folds in the current poll, and writes it back. Columns:

| column | meaning |
| --- | --- |
| `interface` | the NTP port this IP was seen on |
| `address` | the client IP (the key — one row per IP) |
| `times_seen` | polls in which this IP appeared (+1 per poll it's in) |
| `polls_observed` | polls since this IP was first seen (+1 **every** poll). `times_seen / polls_observed` is how consistently the client is present |
| `last_seen_utc` | UTC timestamp of the most recent poll it appeared in |
| `elapsed_seconds` | value from that most recent poll (frozen at last sighting) |
| `avg_percent` | mean of the appliance's `%` over `polls_observed` polls, **counting a poll the IP was absent from as 0** (2 dp) |
| `peak_percent` | the highest `%` ever recorded for this IP — only a real sighting raises it |

Every run advances every row: `polls_observed` +1 for all, and `avg_percent`
gets this poll's `%` for IPs that are present or `0` for those that aren't. An
absent IP's `times_seen`, `last_seen_utc`, `elapsed_seconds` and `peak_percent`
are left alone. Row order is stable: existing rows first, new IPs appended in the
order the appliance listed them.

The appliance's `%` is each client's share of recent NTP query load — it sums to
~100 across the listed clients and is dominated by clients in their initial
fast-poll phase. Because absent polls fold in a `0`, `avg_percent` is the
client's **average share of load since it was first seen**, directly comparable
across rows:

- a client persistently well above the ~100/N fair share is polling abnormally
  often — a stuck clock, a low `minpoll`, or something abusive;
- a client that has left slowly decays toward `avg_percent` 0 — `peak_percent`
  and `last_seen_utc` are then the "was it ever heavy" / "how stale is this"
  signals;
- a flickering client's `avg_percent` sits low in proportion to how rarely it's
  actually present (`times_seen / polls_observed`).

Both `avg_percent` and `peak_percent` restart whenever the register is recreated,
so give them a day or so of polls before reading into them.

> **Caveat:** "absent ⇒ 0" assumes the appliance drops a client from the list 
> when idle.

> **Keep the logging host's own clock synchronised (NTP).** `last_seen_utc` and
> the log timestamps come from *this machine's* clock, not the appliance's. If
> the host clock drifts, `last_seen_utc` is wrong for every row. On Linux/WSL
> check with `timedatectl` (`System clock synchronized: yes`); enable with `sudo
> timedatectl set-ntp true` or run `chrony` / `systemd-timesyncd`. On Windows,
> `w32tm /query /status`.

> **Upgrading:** the code refuses to touch a file whose columns don't match the
> current schema — an old append-style snapshot (`timestamp_utc,…,percent`) or a
> register from an earlier column set. It logs an `ERROR` naming the mismatch and
> exits non-zero without writing. Move the file aside
> (`mv ntp_clients_ntp01r.csv ntp_clients_ntp01r.csv.bak`) and the next run
> starts a fresh register.

**Exit code:** `0` on success (a valid but empty client list still counts as
success). `1` if the SSH session fails after retries, credentials/key are bad,
or any configured interface name isn't recognised by the appliance — good
interfaces are still logged in that last case. Transient connection failures are
retried automatically (`ssh.retries`, `ssh.retry_backoff_seconds`); tune the
per-command wait with `ssh.command_wait_seconds` if tables come back truncated.
See `config.example.yaml` for all the knobs.

If `device_info_command` is set, the top of each register carries a `#`-comment
block with the appliance's `show system` details, e.g.:

```
# ntp client log — device info captured (UTC 2026-09-01T22:41:10+00:00)
# [System information]
#   Model name: Net.Time
#   Serial number: EXAMPLE1
#   Firmware version: NTi-007E/NTiS-107E
...
interface,address,times_seen,polls_observed,last_seen_utc,elapsed_seconds,avg_percent,peak_percent
```

The block is rewritten on every run, so it always reflects the latest `show
system`. Point pandas at the file with `read_csv(path, comment="#")`; the stdlib
`csv` module needs the `#` lines filtered out manually.

## 5. Schedule with cron

The script is one-shot: each run polls every interface once, updates the
registers, and exits. cron is what makes it recurring — it launches a fresh run
on each tick. The interval is **not** in `config.yaml`; it lives in the crontab
line itself.

### Steps

1. Open your crontab:

   ```bash
   crontab -e
   ```

2. Add one line. Use **absolute paths** (cron runs with a bare environment and no
   fixed working directory); pointing at `.venv/bin/python3` directly means you
   don't need to activate the venv:

   ```
   */15 * * * * /path/to/ntpLogger/.venv/bin/python3 /path/to/ntpLogger/ntp_client_logger.py --config /path/to/ntpLogger/config.yaml >> /path/to/ntpLogger/cron.log 2>&1
   ```

   The trailing `>> …/cron.log 2>&1` captures each run's stdout/stderr (the same
   lines that also go to `logging.log_path`). Rotate or truncate that file
   occasionally so it doesn't grow without bound.

3. Save and exit — cron reloads automatically. Confirm with:

   ```bash
   crontab -l
   ```

### Choosing the interval

The first five fields are `minute hour day-of-month month day-of-week`. Change the
schedule by editing those; the command stays the same.

| Cadence | Fields |
| --- | --- |
| every 5 minutes | `*/5 * * * *` |
| every 15 minutes | `*/15 * * * *` |
| every 30 minutes | `*/30 * * * *` |
| hourly, on the hour | `0 * * * *` |
| every 2 hours | `0 */2 * * *` |
| hourly, 09:00–17:00 on weekdays | `0 9-17 * * 1-5` |

### WSL note

On WSL, cron is usually not running by default and only runs while a WSL instance
is open:

```bash
sudo service cron status
sudo service cron start      # if stopped
```

For genuinely unattended logging on Windows, either enable `systemd` in
`/etc/wsl.conf` and `systemctl enable --now cron`, or use Windows Task Scheduler
to invoke `wsl.exe -e /path/to/ntpLogger/.venv/bin/python3 …` on a schedule
instead.

### Without cron (foreground loop)

For a quick ad-hoc poller, loop the one-shot script in the shell:

```bash
while true; do
  ./.venv/bin/python ntp_client_logger.py --config config.yaml
  sleep 900          # interval, in seconds
done
```

Caveats vs. cron: it stops when the terminal closes, you log out, or the machine
reboots (wrap it in `nohup … &`, `tmux`, or `screen` to survive a disconnect);
there's no catch-up after downtime and no locking, though runs are short (~7 s)
so overlap isn't a concern at sane intervals. `Ctrl-C` stops it. For anything
long-lived, prefer cron / systemd / Task Scheduler above.

## Project layout

The logic lives in the `ntp_logger/` package; `ntp_client_logger.py` at the root
is a thin shim so the commands above (and your cron entry) keep working
unchanged — `./.venv/bin/python -m ntp_logger ...` is equivalent.

- `parsing.py` — parse `show ntp <iface> clients` and `show system` output
- `ssh_session.py` — SSH connect + retry + interactive-shell command execution
- `register.py` — the per-IP register: load / fold in a poll / render
- `output.py` — register file I/O (atomic rewrite, device-info comment header)
- `config.py` — load `config.yaml`
- `cli.py` — argument parsing, logging setup, the poll→parse→register pipeline

Appliance output formats vary by firmware; `parse_client_list()` /
`parse_system_info()` in `parsing.py` are the parts to adjust if yours differs.

## Notes

- Uses SSH key auth by default (recommended over password in config.yaml).
- Make sure the key file has correct permissions: `chmod 600 id_rsa`.
- The script uses an interactive SSH shell (not `exec_command`), since many
  appliance CLIs need an interactive session/menu rather than a single
  non-interactive command. If your appliance supports a direct one-shot SSH
  command (`ssh admin@host "show clients"`), `run_remote_session` in
  `ntp_logger/ssh_session.py` could be simplified considerably.
- **Connection lifecycle:** each run opens one fresh SSH connection, runs
  `pre_commands` → `device_info_command` → one `show ntp <iface> clients` per
  interface over a single interactive shell, then closes the shell channel and
  the connection (in a `finally`, so it closes on failure too). A retry opens a
  brand-new connection. Nothing is held open between runs, and parsing + CSV
  writing happen after the connection is already closed — total hold time is
  roughly the sum of `ssh.command_wait_seconds` (~7 s with defaults). There is
  no persistent/pooled session.
- Logs (connection errors, per-poll register stats) go to stdout and to
  `logging.log_path`. Timestamps are in the host's local time by default; set
  `logging.utc: true` to stamp them in UTC (with a ` UTC` marker) so they line up
  with the register's `last_seen_utc` column.

## License

Apache License 2.0 — see [`LICENSE`](LICENSE).

Copyright [year] [your name].
