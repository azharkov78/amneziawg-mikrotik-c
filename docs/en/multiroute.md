# Multiple routes in one container

The optional C supervisor runs one stock AWG proxy process per route, with separate
ports, servers and AWG profiles in one container. Extracted from multi1/multi2,
it does not change packet transformation and does not require multi-client normal
(PR #80). Each normal process still serves one native WG client. Assign a separate
route/port to each client or alternate path. Stock server mode can serve multiple
clients connected to one WG endpoint.

```text
Router WG → container:51821 → AWG VPS A → DC WireGuard
          → container:51822 → AWG VPS B → DC WireGuard
```

A VPS can run native AWG or a stock server proxy whose AWG_REMOTE points to the DC's
WG endpoint. Both sides need matching AWG profiles. Private WG keys remain at WG
endpoints; the supervisor does not terminate the tunnel.

## Files and precedence

Mount `/config` containing optional `common.env` and route files such as `pl.env`
and `msk.env`. See `examples/multiroute/`; replace example addresses, public keys
and H/S/J values before use.

- Route files override common.env. Set AWG_CONFIG_DIR=/config or pass
  --config-dir /config (the command-line argument takes precedence).
- Other inherited AWG_* settings are cleared in directory mode: container env
  settings do not override files. AWG_PROXY_BIN selects the engine executable
  (default /awg-proxy); AWG_ROUTE_NAME is set by the supervisor.
- One AWG_NAME=value per line. Matching outer quotes are stripped; values remain
  literal. No shell, variable expansion, escape processing, export, continuation
  lines or inline comments. Comments start with # on separate lines.
- Duplicate keys within one file are errors. Files are loaded at startup only;
  restart after edits. Rename a route from .env to .disabled to disable it.
- normal/reverse require AWG_CLIENT_PUB. Key lists work in stock server mode.
  AWG_REMOTE still supports port lists/ranges.
- Use distinct AWG_LISTEN ports and AWG_STATE_FILE paths. All processes share
  /etc/resolv.conf: use the same AWG_DNS or configure DNS externally. Relative file
  paths resolve from the process working directory, not the config directory;
  use absolute paths.

--check validates file structure, required fields and identical AWG_LISTEN strings.
It does not validate keys, all engine settings, DNS, port availability or remote
reachability. Wildcards and alternative spellings of the same address may still
conflict at bind. The parser's 64-route limit is not a router capacity recommendation.

## Build and run

The additional Dockerfile does not replace the default image or release workflows:

```sh
docker build -f Dockerfile.multiroute -t awg-proxy:supervisor-dev .
docker run --rm -e AWG_CONFIG_DIR=/config \
  -v "$PWD/examples/multiroute:/config:ro" awg-proxy:supervisor-dev --check
docker run -d --name awg-multi --restart unless-stopped \
  -e AWG_CONFIG_DIR=/config -v "$PWD/examples/multiroute:/config:ro" \
  -p 51821:51821/udp -p 51822:51822/udp awg-proxy:supervisor-dev
```

Without Docker: make build build-supervisor, then run
AWG_PROXY_BIN=/absolute/path/awg-proxy /absolute/path/awg-supervisor --config-dir /config.
Without AWG_CONFIG_DIR/--config-dir, the supervisor execs the stock proxy with the
original environment; no supervisor process remains.

Cross-build static musl binaries with Zig 0.14.1:

```sh
python3 tools/build-multiroute.py --arch armv7
python3 tools/build-multiroute.py --arch arm64
python3 tools/build-multiroute.py --arch amd64
```

Add --image to build a scratch image using Dockerfile.multiroute-prebuilt. ARMv7
is for ac2, ARM64 for ax2/ax3 and Linux ARM64, AMD64 for x86-64 Linux. Export RouterOS
container archives in a format supported by your RouterOS version. This PR does
not publish releases or registry tags.

## MikroTik, resources and lifecycle

Mount the config directory at /config and add AWG_CONFIG_DIR=/config to the
container Envlist. The container has one IP; WG peer endpoint-port selects a route.
Netwatch can change that port, but the supervisor does not probe tunnel health or
switch routes. VPS addresses must remain reachable through independent WAN routes.

Each route has its own threads, sockets and buffers. Memory grows with process
count; on ac2 start small and measure RAM, CPU and latency under load. Different
AWG versions can run in different processes concurrently. This does not aggregate
path bandwidth.

Only an exited child restarts, after 1, 2, 4, 8, 16, then 30 seconds; the delay resets
after 30 seconds of runtime. This monitors process liveness, not tunnel health.
SIGTERM/SIGINT stops all routes. Remaining children receive SIGKILL after five
seconds and are reaped with waitpid. Use a container stop timeout above five seconds.
Engine logs include route names.

## Local validation

make test test-supervisor test-multiroute-transport covers stock C tests, config
isolation, --config-dir precedence, legacy exec startup, isolated restart, forced
shutdown of an unresponsive child, literal config values and 200 WG-shaped datagrams
through two independent normal/reverse routes. This does not test Noise, hardware
throughput or cookie challenges. No CI tests are added.

ARMv5: the supervisor itself builds for ARMv5, but the Zig engine builder supports
ARMv7/ARM64/AMD64 only: Clang rejects stock proxy.c atomics for ARMv5. For a full
ARMv5 build use GCC/musl in an ARMv5 environment with
`make build build-supervisor CC=musl-gcc`. A full ARMv5 image was not validated here.
