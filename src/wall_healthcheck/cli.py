"""Command-line entry point: arguments in, one JSON document out.

Every run that gets past argument parsing prints exactly one JSON document,
even when something unexpected goes wrong.
"""

import argparse
import asyncio
import ipaddress
import logging
import sys
from dataclasses import dataclass
from typing import Any

from wall_healthcheck import __version__, probes
from wall_healthcheck.envelope import Run, emit

TOOL_NAME = "wall-healthcheck"
MAX_CONCURRENT = 100
log = logging.getLogger(TOOL_NAME)


@dataclass(frozen=True)
class Target:
    ip: str
    port: int | None  # None: ICMP echo; a number: TCP connect to that port

    @property
    def method(self) -> str:
        return "icmp" if self.port is None else "tcp"


def target(value: str) -> Target:
    """argparse type: IPv4 address (ping) or IP:PORT (TCP connect)."""
    host, colon, port = value.partition(":")
    try:
        ip = str(ipaddress.IPv4Address(host))
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an IPv4 address: {value!r}") from None
    if not colon:
        return Target(ip, None)
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise argparse.ArgumentTypeError(f"invalid port in {value!r}")
    return Target(ip, int(port))


def bounded_int(low: int, high: int):
    def parse(value: str) -> int:
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"must be between {low} and {high}")
        return number

    return parse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description="Check whether hosts answer ping, or accept a TCP connection, "
        "and how fast. Prints the results as JSON.",
    )
    parser.add_argument(
        "targets",
        nargs="+",
        type=target,
        metavar="TARGET",
        help="IPv4 address to ping, or IP:PORT for a TCP connect check",
    )
    parser.add_argument(
        "--attempts",
        type=bounded_int(1, 10),
        default=3,
        metavar="N",
        help="probes per target (default: %(default)s)",
    )
    parser.add_argument(
        "--timeout-ms",
        type=bounded_int(1, 60_000),
        default=1000,
        metavar="MS",
        help="how long to wait for each probe (default: %(default)s)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="log progress to stderr")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def check_entry(target: Target, probe: probes.ProbeResult) -> dict[str, Any]:
    """One entry of result.checks, as described in schema.json."""
    up = bool(probe.rtts_ms)
    return {
        "ip": target.ip,
        "method": target.method,
        "port": target.port,
        "up": up,
        "successes": len(probe.rtts_ms),
        "rtt_ms": round(sum(probe.rtts_ms) / len(probe.rtts_ms), 2) if up else None,
        "down_reason": None if up else (probe.last_failure or "timeout"),
    }


async def check_all(targets: list[Target], attempts: int, timeout_s: float) -> list[dict]:
    """Check all targets concurrently; the results keep the order of the targets."""
    limit = asyncio.Semaphore(MAX_CONCURRENT)

    async def check(target: Target) -> dict[str, Any]:
        async with limit:
            if target.port is None:
                probe = await probes.ping(target.ip, attempts, timeout_s)
            else:
                probe = await probes.tcp_connect(target.ip, target.port, attempts, timeout_s)
        log.info("%s %s: %d/%d", target.method, target.ip, len(probe.rtts_ms), attempts)
        return check_entry(target, probe)

    return list(await asyncio.gather(*(check(t) for t in targets)))


def collect(args: argparse.Namespace, run: Run) -> dict[str, Any] | None:
    """Run the checks and return the result object, or record an error and return None."""
    if any(t.port is None for t in args.targets):
        try:
            probes.check_icmp_available()
        except probes.IcmpUnavailable as exc:
            run.error(exc.code, exc.message)
            return None
    checks = asyncio.run(check_all(args.targets, args.attempts, args.timeout_ms / 1000))
    return {"checks": checks}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        stream=sys.stderr,
        format="%(name)s: %(message)s",
    )
    run = Run(TOOL_NAME, __version__, {"attempts": args.attempts, "timeout_ms": args.timeout_ms})
    try:
        result = collect(args, run)
    except Exception as exc:
        log.exception("unexpected error")
        run.error("internal_error", f"{type(exc).__name__}: {exc}")
        result = None
    return emit(run.finish(result))
