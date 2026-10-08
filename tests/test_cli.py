"""The command line: target parsing, aggregation of probe results, and one valid
document every time. The probes are replaced by fakes here; test_probes.py
runs the real ones."""

import json
import shutil
import socket
import subprocess
import sysconfig

import pytest

from wall_healthcheck import cli, probes
from wall_healthcheck.probes import IcmpUnavailable, ProbeResult

# Probe results that must produce the result in tests/fixtures/ok.json.
EXAMPLE_PROBES = {
    ("192.168.1.1", None): ProbeResult([1.40, 1.42, 1.44]),
    ("192.168.1.20", 445): ProbeResult([0.86, 0.88, 0.90]),
    ("192.168.1.50", None): ProbeResult([4.0, 4.2], last_failure="timeout"),
    ("192.168.1.60", None): ProbeResult([], last_failure="timeout"),
    ("192.168.1.21", 3389): ProbeResult([], last_failure="refused"),
}
EXAMPLE_TARGETS = [
    "192.168.1.1",
    "192.168.1.20:445",
    "192.168.1.50",
    "192.168.1.60",
    "192.168.1.21:3389",
]


@pytest.fixture
def fake_probes(monkeypatch):
    """Replace both probes with lookups in EXAMPLE_PROBES; record the calls."""
    calls = []

    async def ping(ip, attempts, timeout_s):
        calls.append(("icmp", ip, None, attempts, timeout_s))
        return EXAMPLE_PROBES[(ip, None)]

    async def tcp_connect(ip, port, attempts, timeout_s):
        calls.append(("tcp", ip, port, attempts, timeout_s))
        return EXAMPLE_PROBES[(ip, port)]

    monkeypatch.setattr(probes, "ping", ping)
    monkeypatch.setattr(probes, "tcp_connect", tcp_connect)
    monkeypatch.setattr(probes, "check_icmp_available", lambda: None)
    return calls


def invoke(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def test_example_result(capsys, fake_probes, validator, example):
    code, doc = invoke(capsys, *EXAMPLE_TARGETS)

    validator.validate(doc)
    assert code == 0
    assert doc["params"] == example("ok.json")["params"]
    assert doc["result"] == example("ok.json")["result"]


def test_probe_settings_are_passed_on(capsys, fake_probes):
    invoke(capsys, "192.168.1.1", "192.168.1.20:445", "--attempts", "5", "--timeout-ms", "250")

    assert fake_probes == [
        ("icmp", "192.168.1.1", None, 5, 0.25),
        ("tcp", "192.168.1.20", 445, 5, 0.25),
    ]


def test_icmp_not_permitted(capsys, fake_probes, monkeypatch, validator, example):
    def not_permitted():
        raise IcmpUnavailable("icmp_not_permitted", example("error.json")["errors"][0]["message"])

    monkeypatch.setattr(probes, "check_icmp_available", not_permitted)
    code, doc = invoke(capsys, "192.168.1.1", "192.168.1.20:445")

    validator.validate(doc)
    assert code == 1
    assert doc["errors"] == example("error.json")["errors"]
    assert fake_probes == []


def test_tcp_only_runs_do_not_need_icmp(capsys, fake_probes, monkeypatch, validator):
    def unavailable():
        raise IcmpUnavailable("icmp_not_supported", "no ping sockets here")

    monkeypatch.setattr(probes, "check_icmp_available", unavailable)
    code, doc = invoke(capsys, "192.168.1.20:445")

    validator.validate(doc)
    assert code == 0
    assert doc["result"]["checks"][0]["up"] is True


def test_unexpected_exception_still_prints_valid_json(capsys, monkeypatch, validator):
    def fail(args, run):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(cli, "collect", fail)
    code, doc = invoke(capsys, "192.168.1.1")

    validator.validate(doc)
    assert code == 1
    assert doc["errors"][0]["code"] == "internal_error"


@pytest.mark.parametrize(
    "argv",
    [
        ["printer.lan"],
        ["192.168.1.300"],
        ["192.168.1.1:0"],
        ["192.168.1.1:70000"],
        ["192.168.1.1:ssh"],
        ["192.168.1.1", "--attempts", "0"],
        ["192.168.1.1", "--timeout-ms", "60001"],
    ],
    ids=["hostname", "bad-ip", "port-0", "port-too-big", "port-name", "zero-attempts", "timeout"],
)
def test_bad_arguments(capsys, fake_probes, argv):
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)

    assert exc.value.code == 2
    assert capsys.readouterr().out == ""
    assert fake_probes == []


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_installed_command(validator):
    exe = shutil.which(cli.TOOL_NAME, path=sysconfig.get_path("scripts"))
    assert exe, 'console script not found: run pip install -e ".[dev]"'

    # A TCP check of a closed local port: works on any OS, without network access.
    # The long timeout is for Windows, which retries a refused localhost connection
    # for about two seconds before reporting it.
    target = f"127.0.0.1:{free_port()}"
    proc = subprocess.run(
        [exe, target, "--attempts", "1", "--timeout-ms", "5000"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout)
    validator.validate(doc)
    assert doc["result"]["checks"][0]["down_reason"] == "refused"
