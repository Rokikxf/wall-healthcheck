"""The real probes, against 127.0.0.1 only.

The ICMP tests need Linux ping sockets. They skip elsewhere, unless
WALL_HEALTHCHECK_REQUIRE_ICMP=1, which CI sets so that ICMP problems fail the
build instead of being skipped.
"""

import asyncio
import errno
import os
import socket

import pytest

from wall_healthcheck import probes
from wall_healthcheck.probes import IcmpUnavailable, failure_reason, is_reply_to

# --- packets and errors ---


def test_echo_request_layout():
    packet = probes.echo_request(7)
    assert packet[0] == 8  # echo request
    assert packet[1] == 0  # code
    assert int.from_bytes(packet[6:8], "big") == 7  # sequence number
    assert packet.endswith(probes.PAYLOAD)


def test_reply_matching():
    reply = probes.HEADER.pack(0, 0, 0x1234, 0x0042, 7) + probes.PAYLOAD
    assert is_reply_to(reply, 7)
    assert not is_reply_to(reply, 8)  # a late reply to another attempt
    assert not is_reply_to(probes.echo_request(7), 7)  # a request, not a reply
    assert not is_reply_to(b"\x00\x00", 7)  # too short


@pytest.mark.parametrize(
    "exc, reason",
    [
        (TimeoutError(), "timeout"),
        (ConnectionRefusedError(), "refused"),
        (OSError(errno.EHOSTUNREACH, "No route to host"), "host_unreachable"),
        (OSError(errno.ENETUNREACH, "Network is unreachable"), "network_unreachable"),
        (OSError(errno.EPERM, "Operation not permitted"), "network_error"),
    ],
)
def test_failure_reasons(exc, reason):
    assert failure_reason(exc) == reason


# --- TCP, on any OS ---


def test_tcp_open_port():
    async def scenario():
        server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            return await probes.tcp_connect("127.0.0.1", port, attempts=3, timeout_s=2)

    result = asyncio.run(scenario())
    assert len(result.rtts_ms) == 3
    assert all(rtt >= 0 for rtt in result.rtts_ms)


def test_tcp_closed_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    # 5 s: Windows retries a refused localhost connection for about two seconds.
    result = asyncio.run(probes.tcp_connect("127.0.0.1", port, attempts=1, timeout_s=5))
    assert result.rtts_ms == []
    assert result.last_failure == "refused"


# --- ICMP, Linux only ---


def icmp_unavailable_reason() -> str | None:
    try:
        probes.check_icmp_available()
    except IcmpUnavailable as exc:
        return exc.message
    return None


ICMP_PROBLEM = icmp_unavailable_reason()
needs_icmp = pytest.mark.skipif(
    ICMP_PROBLEM is not None and os.environ.get("WALL_HEALTHCHECK_REQUIRE_ICMP") != "1",
    reason=f"ICMP not available: {ICMP_PROBLEM}",
)


@needs_icmp
def test_ping_localhost():
    assert ICMP_PROBLEM is None, ICMP_PROBLEM
    result = asyncio.run(probes.ping("127.0.0.1", attempts=3, timeout_s=1))
    assert len(result.rtts_ms) == 3
    assert result.last_failure is None


@needs_icmp
def test_ping_unanswered():
    assert ICMP_PROBLEM is None, ICMP_PROBLEM
    # 192.0.2.0/24 is reserved for documentation (RFC 5737), so nobody answers.
    # The failure is either a timeout or an unreachable error, depending on routing.
    result = asyncio.run(probes.ping("192.0.2.1", attempts=1, timeout_s=0.5))
    assert result.rtts_ms == []
    assert result.last_failure in {"timeout", "host_unreachable", "network_unreachable"}
