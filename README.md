# ALBEDO Net.Time Client Logger

![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)
![License](https://img.shields.io/badge/license-Apache%202.0-green)

Polls an ALBEDO Net.Time over SSH on a schedule and maintains a per-interface
**client register** — one CSV row per client IP, tracking how many polls it has
appeared in (`times_seen`), when it was last seen, and its share of the
Net.Time's NTP query load (`avg_percent` / `peak_percent`). This tool is
built specifically against the ALBEDO Net.Time CLI (`show ntp <interface>
clients`) and is not expected to work against other vendors' appliances.

- One register file per NTP port/interface, not a growing snapshot log
- Tracks presence, load share, and staleness per client IP over time
- Runs one-shot; scheduled via cron (Linux/WSL) or Task Scheduler (Windows)
- Retries transient SSH failures; refuses to silently corrupt an old-schema file
- Optional device-info header (`show system`) written into each CSV

Requires **Python 3.9+**. Runs on Linux, macOS, and Windows.

## Contents

- [Install](#1-install)
- [Configure](#2-configure)
- [Verify against your Net.Time](#3-verify-against-your-nettime-do-this-first)
- [Run it](#4-run-it)
- [Understand the output](#understanding-the-register)
- [Schedule it](#5-schedule-it)
- [Project layout](#project-layout)
- [Troubleshooting](#troubleshooting)
- [Security notes](#security-notes)
- [License](#license)

## 1. Install

**Linux / macOS:**

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

**Windows (PowerShell):**

```powershell
python -m venv .venv
.venv\Scripts\pip.exe install -r requirements.txt
```

All commands in this README call the venv's Python directly
(`./.venv/bin/python` on Linux/macOS, `.venv\Scripts\python.exe` on Windows),
so they work from any shell without activating the venv first — substitute
whichever matches your platform as you go.

## 2. Configure

Copy the template and edit it (`config.yaml` is gitignored — it holds credentials):

```bash
cp config.example.yaml config.yaml
```

Minimum you'll need to set:

```yaml
ssh:
  host: 203.0.113.10
  username: admin
  private_key_path: ~/.ssh/id_ed25519   # or use `password:` instead

interfaces:
  - ntp01r
  - ntp02r        # up to 4 — whatever's active on this Net.Time

output:
  csv_dir: ./data

device_info_command: show system         # optional; blank to skip
```

- `interfaces`: the physical NTP ports active on **this specific** Net.Time
  (e.g. `ntp01`, `ntp01r`). Each one gets its own command run and its own CSV
  file — this is the per-environment variable, so it lives in config, not in
  the script.
- `output.csv_dir`: folder where the per-interface CSVs are written.
- `device_info_command` (optional): a command run once per session whose
  output describes the Net.Time (default `show system`). Its parsed
  key/value output is written as a `#`-comment block at the top of each CSV
  when the file is first created. Leave blank to skip.
- `ssh.retries`, `ssh.retry_backoff_seconds`, `ssh.command_wait_seconds`:
  tune connection retry behavior and per-command wait (increase the latter if
  tables come back truncated). See `config.example.yaml` for the full set of
  knobs, including `logging.log_path` and `logging.utc`.

**One important behavior to know before your first scheduled run:** if
`interfaces` contains a name the Net.Time doesn't recognize, that one
interface fails but the others still log normally — see
[Troubleshooting](#troubleshooting) for how that's reported.

The command itself (`show ntp <interface> clients`) is already wired up via
`command_template` — you shouldn't need to touch that unless the syntax
differs on your firmware version.

## 3. Verify against your Net.Time (do this first!)

```bash
./.venv/bin/python ntp_client_logger.py --config config.yaml --raw
```

This prints the raw SSH output for `device_info_command` (if set) and for every
interface listed in `interfaces`. `--raw` is a dump only — it does not validate
interface names or exit non-zero on a bad one; use `--dry-run` for that.
Confirm it matches the expected table format (Address / Elapsed / % columns):

```
NTP clients list ETH(P)
Address                 Elapsed                  %
--------------------------------------------------
192.0.2.1              00:01:00                 94
4 NTP clients listed
```

If your Net.Time's output differs at all (extra header lines, different
column order, etc.), adjust `parse_client_list()` in `ntp_logger/parsing.py`
accordingly.

Then validate for real, without writing to CSV:

```bash
./.venv/bin/python ntp_client_logger.py --config config.yaml --dry-run
```

`--dry-run` also validates the `interfaces` list: if the Net.Time doesn't
recognise a name it logs an `ERROR` (quoting the Net.Time's own reply) and the
command exits non-zero, so a typo is caught before the first scheduled run.

## 4. Run it

```bash
./.venv/bin/python ntp_client_logger.py --config config.yaml
```

Each interface has one **register** file (e.g. `ntp_clients_ntp01r.csv`) — one
row per client IP, not a growing log of snapshots. Every run reads the file,
folds in the current poll, and writes it back. A row looks like this:

```
interface,address,times_seen,polls_observed,last_seen_utc,elapsed_seconds,avg_percent,peak_percent
ntp01r,192.0.2.1,14,15,2026-09-03T22:41:10+00:00,60,61.33,94.00
```

### Understanding the register

| column | meaning |
| --- | --- |
| `interface` | the NTP port this IP was seen on |
| `address` | the client IP (the key — one row per IP) |
| `times_seen` | polls in which this IP appeared (+1 per poll it's in) |
| `polls_observed` | polls since this IP was first seen (+1 **every** poll). `times_seen / polls_observed` is how consistently the client is present |
| `last_seen_utc` | UTC timestamp of the most recent poll it appeared in |
| `elapsed_seconds` | value from that most recent poll (frozen at last sighting) |
| `avg_percent` | mean of the Net.Time's `%` over `polls_observed` polls, **counting a poll the IP was absent from as 0** (stored at full precision — round it yourself for display) |
| `peak_percent` | the highest `%` ever recorded for this IP — only a real sighting raises it |

Every run advances every row: `polls_observed` +1 for all, and `avg_percent`
gets this poll's `%` for IPs that are present or `0` for those that aren't. An
absent IP's `times_seen`, `last_seen_utc`, `elapsed_seconds` and `peak_percent`
are left alone. Row order is stable: existing rows first, new IPs appended in the
order the Net.Time listed them.

The Net.Time's `%` is each client's share of recent NTP query load — it sums to
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

**`avg_percent` does not add up to 100 down the column** — only the Net.Time's
live `%` does, and only within a single poll. Each row is divided by its own
`polls_observed`, which starts when that IP was first seen, so the rows cover
different time spans; a client that has left keeps a slowly-decaying row that
still counts; and a brand-new client (`polls_observed` = 1) contributes its full
current `%` with no dilution. So the column total drifts around 100 rather than
equalling it, and runs well above 100 for a while after clients churn. For a
"share of load right now" figure that does sum to ~100, read the Net.Time's live
`%` (`--raw` / `--dry-run`), not the register.

Both `avg_percent` and `peak_percent` restart whenever the register is recreated,
so give them a day or so of polls before reading into them.

If `device_info_command` is set, the top of each register carries a `#`-comment
block with the Net.Time's `show system` details, e.g.:

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

**Exit code:** `0` on success (a valid but empty client list still counts as
success). `1` if the SSH session fails after retries, credentials/key are bad,
or any configured interface name isn't recognised by the Net.Time — good
interfaces are still logged in that last case.

## 5. Schedule it

The script is one-shot: each run polls every interface once, updates the
registers, and exits. The scheduler is what makes it recurring — it launches a
fresh run on each tick. The interval is **not** in `config.yaml`; it lives in
the scheduler entry itself.

### Windows (Task Scheduler)

1. Open **Task Scheduler** → **Create Task…**
2. **General** tab: name it, and check "Run whether user is logged on or not"
   if you want it to run unattended.
3. **Triggers** tab → **New…** → set it to repeat on your chosen interval
   (e.g. every 15 minutes, indefinitely).
4. **Actions** tab → **New…** → **Start a program**:
   - Program/script: full path to `.venv\Scripts\python.exe`
   - Add arguments: `ntp_client_logger.py --config config.yaml`
   - Start in: the project folder (so relative paths in `config.yaml` resolve)
5. Save. Test it once with **Run** in the Task Scheduler UI before trusting the
   schedule, and check `logging.log_path` for output.

### Linux / macOS (cron)

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

**Choosing the interval:** the first five fields are `minute hour day-of-month
month day-of-week`. Change the schedule by editing those; the command stays
the same.

| Cadence | Fields |
| --- | --- |
| every 5 minutes | `*/5 * * * *` |
| every 15 minutes | `*/15 * * * *` |
| every 30 minutes | `*/30 * * * *` |
| hourly, on the hour | `0 * * * *` |
| every 2 hours | `0 */2 * * *` |
| hourly, 09:00–17:00 on weekdays | `0 9-17 * * 1-5` |

### WSL note

On WSL, cron is usually not running by default and only runs while a WSL
instance is open:

```bash
sudo service cron status
sudo service cron start      # if stopped
```

For genuinely unattended logging from WSL, either enable `systemd` in
`/etc/wsl.conf` and `systemctl enable --now cron`, or use native Windows Task
Scheduler (above) to invoke `wsl.exe -e /path/to/ntpLogger/.venv/bin/python3 …`
on a schedule instead.

### Without a scheduler (foreground loop)

For a quick ad-hoc poller, loop the one-shot script in the shell:

```bash
while true; do
  ./.venv/bin/python ntp_client_logger.py --config config.yaml
  sleep 900          # interval, in seconds
done
```

Caveats vs. a real scheduler: it stops when the terminal closes, you log out, or
the machine reboots (wrap it in `nohup … &`, `tmux`, or `screen` to survive a
disconnect); there's no catch-up after downtime and no locking, though runs are
short (~7 s) so overlap isn't a concern at sane intervals. `Ctrl-C` stops it.
For anything long-lived, prefer cron / systemd / Task Scheduler above.

## Project layout

The logic lives in the `ntp_logger/` package; `ntp_client_logger.py` at the root
is a thin shim so the commands above (and your scheduled entry) keep working
unchanged — `./.venv/bin/python -m ntp_logger ...` is equivalent.

- `parsing.py` — parse `show ntp <iface> clients` and `show system` output
- `ssh_session.py` — SSH connect + retry + interactive-shell command execution
- `register.py` — the per-IP register: load / fold in a poll / render
- `output.py` — register file I/O (atomic rewrite, device-info comment header)
- `config.py` — load `config.yaml`
- `cli.py` — argument parsing, logging setup, the poll→parse→register pipeline

Net.Time output formats vary by firmware; `parse_client_list()` /
`parse_system_info()` in `parsing.py` are the parts to adjust if a firmware
revision changes the layout.

Connection lifecycle: each run opens one fresh SSH connection, runs
`pre_commands` → `device_info_command` → one `show ntp <iface> clients` per
interface over a single interactive shell, then closes the shell channel and
the connection (in a `finally`, so it closes on failure too). A retry opens a
brand-new connection. Nothing is held open between runs, and parsing + CSV
writing happen after the connection is already closed — total hold time is
roughly the sum of `ssh.command_wait_seconds` (~7 s with defaults). There is no
persistent/pooled session.

The script uses an interactive SSH shell (not `exec_command`), since the
Net.Time CLI needs an interactive session/menu rather than a single
non-interactive command. If a future firmware revision supports a direct
one-shot SSH command (`ssh admin@host "show clients"`), `run_remote_session`
in `ntp_logger/ssh_session.py` could be simplified considerably.

## Troubleshooting

**"SSH session fails after retries"** — check `ssh.host`/credentials, and that
the key file has correct permissions (`chmod 600 id_rsa`). Transient failures
are retried automatically (`ssh.retries`, `ssh.retry_backoff_seconds`); if
retries are exhausted the process exits `1`. If client tables come back
truncated, increase `ssh.command_wait_seconds`.

**"An interface in `interfaces` isn't recognised"** — `--dry-run` catches this
before your first scheduled run: it logs an `ERROR` quoting the Net.Time's own
reply and exits non-zero. In a real run, that one interface is skipped but
every other configured interface still logs normally, and the process exits
`1` so the failure isn't silent in your cron/Task Scheduler log.

**"The tool refuses to write to an existing CSV"** — it refuses to touch a file
whose columns don't match the current register schema (an old append-style
snapshot, or a register from an earlier column set). It logs an `ERROR` naming
the mismatch and exits non-zero without writing. Move the file aside
(`mv ntp_clients_ntp01r.csv ntp_clients_ntp01r.csv.bak`) and the next run starts
a fresh register.

**"`avg_percent` looks wrong for a client that just left"** — this is expected:
absent polls fold in as `0`, so `avg_percent` decays toward 0 over time rather
than freezing. `peak_percent` and `last_seen_utc` are the right columns to
check "was it ever heavy" or "how stale is this row." This assumes the
Net.Time actually drops idle clients from its list rather than leaving them
listed at `0`.

**"`last_seen_utc` seems off by the wrong amount"** — that timestamp (and log
timestamps) come from *this machine's* clock, not the Net.Time's. Keep the
logging host's own clock NTP-synchronized. On Linux/WSL check with
`timedatectl` (`System clock synchronized: yes`); enable with `sudo
timedatectl set-ntp true` or run `chrony` / `systemd-timesyncd`. On Windows,
`w32tm /query /status`. Set `logging.utc: true` to stamp logs in UTC so they
line up with the register's `last_seen_utc`.

## Security notes

- `config.yaml` holds SSH credentials and is gitignored — don't commit it, and
  double-check before pushing if you've copied values elsewhere.
- Prefer `ssh.private_key_path` over `ssh.password`; restrict the key file to
  `chmod 600`.
- `csv_dir` and the log file reveal internal network topology (client IPs
  talking to this Net.Time) — treat them with the same access control as any
  other internal inventory data.

## License

Apache License 2.0 — see [`LICENSE`](LICENSE).

Copyright 2026 Albedo Telecom.
