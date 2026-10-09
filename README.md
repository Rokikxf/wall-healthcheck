# wall-healthcheck

Checks whether hosts answer: by ping (ICMP echo) or by opening a TCP
connection to a port. Reports up or down, how many probes succeeded and the
average round-trip time, as JSON. It is the monitoring tool of the wall IT
asset management system. The hub runs it every minute or two and sends email
alerts when a device goes down, but it works on its own from the command line.

## Requirements

- Python 3.13
- Linux, for ping (ICMP) checks. TCP checks work on any OS.

## Usage

```
wall-healthcheck TARGET [TARGET ...] [--attempts N] [--timeout-ms MS] [-v] [--version]
```

| Option           | Meaning                                                                      |
|------------------|------------------------------------------------------------------------------|
| `TARGET`         | `192.168.1.1` pings the host. `192.168.1.20:445` opens a TCP connection to port 445. |
| `--attempts N`   | Probes per target, 1–10 (default 3). A target is up if at least one succeeds. |
| `--timeout-ms MS`| How long to wait for each probe (default 1000).                              |
| `-v`             | Log each target's result to stderr.                                          |

All targets are checked at the same time, and the results keep the order in
which the targets were given.

```console
$ wall-healthcheck 192.168.1.1 192.168.1.20:445 192.168.1.60
```

```json
{"ip": "192.168.1.1",  "method": "icmp", "port": null, "up": true,  "successes": 3, "rtt_ms": 1.42, "down_reason": null}
{"ip": "192.168.1.20", "method": "tcp",  "port": 445,  "up": true,  "successes": 3, "rtt_ms": 0.88, "down_reason": null}
{"ip": "192.168.1.60", "method": "icmp", "port": null, "up": false, "successes": 0, "rtt_ms": null, "down_reason": "timeout"}
```

These are the entries of `result.checks`. Complete documents are in
[tests/fixtures/](tests/fixtures/).

### Ping or TCP?

Windows PCs usually drop ping: the Windows Defender Firewall rule *File and
Printer Sharing (Echo Request - ICMPv4-In)* is off unless file sharing has been
turned on for that network. Check them with a TCP
port that is open, such as 445 (file sharing) or 3389 (Remote Desktop).
For TCP checks, a refused connection counts as **down**: the host is reachable,
but the service on that port is not.

## Ping without root

Ping uses Linux *ping sockets*, which need no root and no capabilities. The
kernel only allows them for the groups in `net.ipv4.ping_group_range`. Ubuntu
Server 24.04 ships with them **disabled** (`1 0`), because its own `ping` uses
file capabilities instead; other distributions differ. Check with
`sysctl net.ipv4.ping_group_range`. When ping sockets are disabled, the run fails
with error code `icmp_not_permitted`. To allow them for all groups, now and
after reboots:

```bash
echo 'net.ipv4.ping_group_range = 0 2147483647' | sudo tee /etc/sysctl.d/60-ping.conf
sudo sysctl -p /etc/sysctl.d/60-ping.conf
```

The implementation is in [probes.py](src/wall_healthcheck/probes.py). It builds
the ICMP echo request itself; with ping sockets, the kernel fills in the
identifier and the checksum.

## Output

Every run that gets past argument parsing prints exactly one JSON document to
stdout, described by [schema.json](schema.json) (schema version 1.0). A host
that is down is a normal result, not an error.

| Exit code | Meaning                                                   |
|-----------|-----------------------------------------------------------|
| 0         | `status` is `ok`: the checks ran (whether hosts are up or down) |
| 1         | `status` is `error` (details in `errors`)                 |
| 2         | invalid arguments: nothing on stdout, usage on stderr     |

| `down_reason`         | Meaning                                                   |
|-----------------------|-----------------------------------------------------------|
| `timeout`             | No answer in time.                                        |
| `refused`             | TCP only: the host answered, but nothing listens on the port. |
| `host_unreachable`    | The network reported the host unreachable.                |
| `network_unreachable` | No route to that network from this machine.               |
| `network_error`       | Any other socket error.                                   |

| Error code            | Cause                                                     |
|-----------------------|-----------------------------------------------------------|
| `icmp_not_permitted`  | Ping sockets not allowed for this user (see above).       |
| `icmp_not_supported`  | Not running on Linux, but ping targets were given.        |
| `internal_error`      | A bug in wall-healthcheck. The traceback is on stderr.    |

The rules shared by all wall-\* tools are in [CONTRACT.md](CONTRACT.md).

## Development

```bash
python -m venv .venv
.venv\Scripts\activate            # Linux: . .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check . && ruff format --check .
```

The real ping tests in `tests/test_probes.py` skip where ping sockets are not
available (e.g. Windows). CI allows ping sockets and sets
`WALL_HEALTHCHECK_REQUIRE_ICMP=1`, so there they must pass. The TCP tests use
local ports and run everywhere.

## Releasing

1. Set `__version__` in `src/wall_healthcheck/__init__.py`, and move the
   `Unreleased` entries in `CHANGELOG.md` under the new version.
2. Commit, then tag and push the tag:

   ```bash
   git tag v0.1.0
   git push origin main v0.1.0
   ```

CI fails a tag that does not match `__version__`. The hub installs an exact
release:

```bash
pip install "wall-healthcheck @ git+https://github.com/Rokikxf/wall-healthcheck@v0.1.0"
```
