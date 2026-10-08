# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/). Changes to the output format also
state the new `schema_version` (see [CONTRACT.md](CONTRACT.md)).

## [Unreleased]

## [0.1.0] - 2026-10-08

First release. Output schema version 1.0.

### Added

- Ping checks (ICMP echo) with Linux ping sockets: no root or capabilities
  needed.
- TCP connect checks (`IP:PORT`), for hosts that drop ping, such as Windows
  PCs.
- All targets checked concurrently; results in the order given, with
  successes, mean round-trip time and the reason a host is down.
- One JSON document per run following `schema.json`, including when ping is
  not available (`icmp_not_permitted`, `icmp_not_supported`).
