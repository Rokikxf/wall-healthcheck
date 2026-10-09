"""The two probes: ICMP echo (ping) and TCP connect.

ICMP uses Linux "ping sockets" (SOCK_DGRAM with IPPROTO_ICMP), which need no
root. The kernel fills in the echo identifier and the checksum, and delivers to
each socket only the replies to its own requests. A process may open one only
if its group is in net.ipv4.ping_group_range. Ubuntu Server 24.04 disables this
by default ("1 0"), so it has to be allowed first (see README). Other systems
are not supported for ICMP; TCP checks work anywhere.
"""

import asyncio
import contextlib
import errno
import socket
import struct
import sys
import time
from dataclasses import dataclass, field

ECHO_REQUEST = 8
ECHO_REPLY = 0
HEADER = struct.Struct("!BBHHH")  # type, code, checksum, identifier, sequence
PAYLOAD = b"wall-healthcheck"
INTERVAL_S = 0.2  # pause between attempts to the same target


@dataclass
class ProbeResult:
    """Round-trip times of the successful attempts, and why the last failure failed."""

    rtts_ms: list[float] = field(default_factory=list)
    last_failure: str | None = None


class IcmpUnavailable(Exception):
    """ICMP sockets cannot be used here. code becomes errors[].code in the output."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def check_icmp_available() -> None:
    """Raise IcmpUnavailable unless this process can open a ping socket."""
    if sys.platform != "linux":
        raise IcmpUnavailable(
            "icmp_not_supported",
            f"ICMP checks need Linux ping sockets, which {sys.platform} does not have. "
            "Use IP:PORT targets for TCP checks.",
        )
    try:
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP).close()
    except PermissionError:
        raise IcmpUnavailable(
            "icmp_not_permitted",
            "This user may not open ICMP sockets. Allow it with sysctl "
            "net.ipv4.ping_group_range, or use IP:PORT targets for TCP checks.",
        ) from None
    except OSError as exc:
        raise IcmpUnavailable("icmp_not_supported", f"Cannot open an ICMP socket: {exc}") from None


def echo_request(seq: int) -> bytes:
    # Checksum and identifier are left at 0: the kernel fills them in for ping sockets.
    return HEADER.pack(ECHO_REQUEST, 0, 0, 0, seq) + PAYLOAD


def is_reply_to(data: bytes, seq: int) -> bool:
    """True if data (an ICMP message, without IP header) is the echo reply for seq."""
    if len(data) < HEADER.size:
        return False
    kind, _code, _checksum, _identifier, reply_seq = HEADER.unpack_from(data)
    return kind == ECHO_REPLY and reply_seq == seq


def failure_reason(exc: OSError) -> str:
    """down_reason for a failed attempt. TimeoutError is an OSError too."""
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, ConnectionRefusedError):
        return "refused"
    return {
        errno.EHOSTUNREACH: "host_unreachable",
        errno.ENETUNREACH: "network_unreachable",
    }.get(exc.errno, "network_error")


async def ping(ip: str, attempts: int, timeout_s: float) -> ProbeResult:
    """Send `attempts` echo requests, one at a time, each waiting up to timeout_s."""
    loop = asyncio.get_running_loop()
    result = ProbeResult()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP) as sock:
        sock.setblocking(False)
        for seq in range(1, attempts + 1):
            if seq > 1:
                await asyncio.sleep(INTERVAL_S)
            start = time.perf_counter()
            try:
                await loop.sock_sendto(sock, echo_request(seq), (ip, 0))
                async with asyncio.timeout(timeout_s):
                    # Skip late replies to earlier attempts.
                    while not is_reply_to(await loop.sock_recv(sock, 1024), seq):
                        pass
                result.rtts_ms.append((time.perf_counter() - start) * 1000)
            except OSError as exc:
                result.last_failure = failure_reason(exc)
    return result


async def tcp_connect(ip: str, port: int, attempts: int, timeout_s: float) -> ProbeResult:
    """Open (and close) `attempts` TCP connections; the RTT is the handshake time."""
    result = ProbeResult()
    for attempt in range(attempts):
        if attempt:
            await asyncio.sleep(INTERVAL_S)
        start = time.perf_counter()
        try:
            async with asyncio.timeout(timeout_s):
                _reader, writer = await asyncio.open_connection(ip, port)
        except OSError as exc:
            result.last_failure = failure_reason(exc)
            continue
        result.rtts_ms.append((time.perf_counter() - start) * 1000)
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()
    return result
