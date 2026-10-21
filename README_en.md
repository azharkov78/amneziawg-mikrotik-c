> This project is not affiliated with or endorsed by MikroTik / SIA Mikrotikls

# awg-proxy -- AmneziaWG for MikroTik

[![C11](https://img.shields.io/badge/C-11-blue)](https://en.cppreference.com/w/c/11)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

[Русская версия](README.md) | [Configurator](https://timbrs.github.io/amneziawg-mikrotik-c/conf3.0.html) | [Releases](https://github.com/timbrs/amneziawg-mikrotik-c/releases) | [FAQ](docs/en/faq.md)

## In short

- **What it is.** A RouterOS container (~300 KB) that connects a regular MikroTik WireGuard interface to an AmneziaWG server. The router does the encryption itself; the container only changes how packets look: headers, padding, junk packets.
- **Protocol.** AmneziaWG 1.0, 1.5, 2.0, 3.0 and 3.1, detected automatically from the `.conf` parameters. A free Cloudflare WARP config works too.
- **RouterOS.** 7.4+ with the `container` package. RouterOS 7.21+ needs `awg-proxy-{arch}.tar.gz` (OCI), 7.20 and older need `awg-proxy-{arch}-7.20-Docker.tar.gz`. On 7.22+ you can install straight from the `ghcr.io/timbrs/awg-proxy:latest` registry and update with one command.
- **Hardware.** arm64, arm (ARMv7), armv5 (hEX refresh, hEX S 2025 and hAP ax S, separate image) and x86_64. **MIPS is not supported** (hEX, hEX S, hAP ac lite, hAP lite and similar): RouterOS has no container package for it. 16 MB of free RAM is enough.
- **How to install.** The [configurator](https://timbrs.github.io/amneziawg-mikrotik-c/conf3.0.html): paste the `.conf` from AmneziaVPN and get ready-to-run MikroTik commands. It runs offline in the browser and sends your keys nowhere.
- **Modes.** Client to an AmneziaWG server (normal), two MikroTiks to each other (site-to-site), a hub for many clients (server).
- **This is the current repository.** The old Go version, [timbrs/amneziawg-mikrotik](https://github.com/timbrs/amneziawg-mikrotik), is no longer developed.

Docs last reviewed on 22 October 2026 for awg-proxy 1.4.3. Tested on RouterOS 7.24.2 (hAP ax², arm64) and hAP ac² (arm) against AmneziaWG 3.1 servers (amneziawg-go and the kernel module).

## Will it run on my router

`/system/resource/print` gives the exact answer in `architecture-name`: `arm64`, `arm` and `x86_64` work; `mipsbe`, `mmips`, `smips`, `tile` and `ppc` do not. The configurator picks the image from the architecture, the RouterOS version and the board model itself.

| Models | Architecture | Image for RouterOS 7.21+ | Notes |
|--------|--------------|--------------------------|-------|
| hAP ax², hAP ax³, RB5009, CCR2004, CCR2116, CCR2216 | arm64 | `awg-proxy-arm64.tar.gz` or `ghcr.io/timbrs/awg-proxy:latest` | Fastest option. Thread pinning and `AWG_RT` are available on 4 cores |
| hAP ac², hAP ac³, RB4011, RB3011 | arm | `awg-proxy-arm.tar.gz` or `ghcr.io/timbrs/awg-proxy:latest` | hAP ac² has 16 MB flash: install to an ext4 USB stick, see [troubleshooting](docs/en/troubleshooting.md#child-spawn-failed--could-not-load-next-layer) |
| hEX refresh (E50UG), hEX S 2025 (E60iUGS), hAP ax S (E62iUGS) | arm, but the CPU runs ARMv5 only | `awg-proxy-armv5.tar.gz` or `ghcr.io/timbrs/awg-proxy:latest-armv5` | The regular arm image dies with `signal 4 (Illegal instruction)` |
| x86, CHR | x86_64 | `awg-proxy-amd64.tar.gz` or `ghcr.io/timbrs/awg-proxy:latest` | |
| hEX (RB750Gr3), hEX S (RB760iGS), hAP ac lite, hAP ac, hAP lite, RB2011, mAP | MIPS | — | **Not supported**: RouterOS has no container package for MIPS |
| CCR1009, CCR1016, CCR1036, CCR1072 | tile | — | **Not supported** |

For RouterOS 7.20 and older use the same names with the `-7.20-Docker` suffix, e.g. `awg-proxy-arm-7.20-Docker.tar.gz`.

## Table of Contents

- [How It Works](#how-it-works)
- [Quick Start (Configurator)](#quick-start-configurator)
- [Installing Without GitHub Access](#installing-without-github-access)
- [Requirements](#requirements)
- [Manual Installation](#manual-installation)
- [Getting AWG Parameters](#getting-awg-parameters)
- [Server Mode (1:N) — Detailed Setup](#server-mode-1n--detailed-setup)
- [Additional Settings](#additional-settings)
- [Updating](#updating)
- [Uninstallation](#uninstallation)
- [Troubleshooting](#troubleshooting)
- [Building from Source](#building-from-source)
- [License](#license)

Detailed documentation lives in separate files in [`docs/en/`](docs/en/) (Russian: [`docs/`](docs/)):

| File | Topic |
|------|-------|
| [faq.md](docs/en/faq.md) | Common questions in plain words: will it run on my router, what speed to expect, how it differs from other containers |
| [install.md](docs/en/install.md) | Where to get the `.conf`, WARP, step-by-step manual install, installing without GitHub access |
| [update.md](docs/en/update.md) | Registry updates, self-repair, moving off an old file install |
| [env.md](docs/en/env.md) | Every container environment variable |
| [protocol.md](docs/en/protocol.md) | AmneziaWG 3.0 and 3.1 on the wire, the fallback chain, IPv6 |
| [routing.md](docs/en/routing.md) | Routes through the tunnel, DNS, MTU and PPPoE, address-list |
| [server-mode.md](docs/en/server-mode.md) | A 1:N hub on a VPS or a MikroTik |
| [troubleshooting.md](docs/en/troubleshooting.md) | Error messages and what to do about them |

A short summary with links for AI assistants: [llms.txt](https://timbrs.github.io/amneziawg-mikrotik-c/llms.txt). Working on the code? See [AGENTS.md](AGENTS.md).

## How It Works

### Normal Mode (default)

```
MikroTik WG client ──UDP──> [awg-proxy] ──UDP──> AmneziaWG server
   (encryption)          (transformation)          (obfuscation)
```

The proxy replaces packet headers, adds padding and junk packets so the AmneziaWG server accepts the traffic. Keys and data are not modified.

### Reverse Mode (site-to-site)

```
MikroTik1 WG ↔ [proxy1 normal] ──AWG──> [proxy2 reverse] ↔ MikroTik2 WG
```

Reverse proxy: accepts AWG traffic from a normal proxy, transforms it back to standard WireGuard, and forwards to a local WG server. Allows connecting two MikroTik routers via AWG without running a separate AWG server.
This is a point-to-point link; multiple connections are not supported.

### Server Mode (hub, 1:N)

```
proxy1a (normal) ──AWG──┐
proxy1b (normal) ──AWG──┤──> [reverse-hub] ──WG──> WG server
proxy1c (normal) ──AWG──┘
```

Multi-client reverse proxy: accepts AWG connections from multiple normal proxies and routes responses from the WG server to the correct client via a built-in session table. Each peer uses ~16 bytes in the hash table.

Detailed setup instructions in [Server Mode (1:N) — Detailed Setup](#server-mode-1n--detailed-setup).

The protocol version (AmneziaWG 1.0–3.1) is detected automatically from the environment variables.

## Quick Start (Configurator)

0. Prepare your router:
   - Install the **container** package from [mikrotik.com](https://mikrotik.com/download) (System → Packages), upload it to the router and reboot
   - Enable device-mode:
     ```routeros
     /system/device-mode/update container=yes fetch=yes bandwidth-test=yes scheduler=yes
     ```
     The router will ask for confirmation (press Reset/Mode button or wait for reboot)
1. Export a `.conf` file from AmneziaVPN (see [Getting AWG Parameters](#getting-awg-parameters))
2. Open the [configurator](https://timbrs.github.io/amneziawg-mikrotik-c/conf3.0.html)
3. Paste the `.conf` file contents
4. Copy the generated commands and run them in MikroTik terminal

Done. The configurator works offline; no data is sent to any server. The same commands are also available without a browser, [from the command line](#command-line-generator).

<video src="https://github.com/user-attachments/assets/f0100789-0a23-42f8-a67f-085e5f8d13a3" controls width="100%"></video>

![Speed test on MikroTik AX3](https://github.com/user-attachments/assets/9fb34444-681b-4f34-8306-8f202f1b121d)

*Speed test on MikroTik AX3*

## Installing Without GitHub Access

Turn on **offline mode** in the configurator: step 0 asks the router for its architecture and RouterOS version and prints which files to download and where to put them. Details — [docs/en/install.md](docs/en/install.md#installing-without-github-access).

## Requirements

- An AmneziaWG server with known obfuscation parameters
- Configuration file `.conf` exported from AmneziaVPN, or a Cloudflare WARP config
- MikroTik RouterOS 7.4+ with the **container** package
  - **RouterOS 7.21+**: standard images `awg-proxy-{arch}.tar.gz` (OCI format)
  - **RouterOS 7.20 and below**: images `awg-proxy-{arch}-7.20-Docker.tar.gz` (Docker format)
  - The configurator detects the version automatically
- Architecture: ARM64, ARM (v7), ARM (v5: hEX refresh / hEX S 2025 / hAP ax S), or x86_64, see the [model table](#will-it-run-on-my-router) ([MikroTik docs](https://help.mikrotik.com/docs/spaces/ROS/pages/84901929/Container))
- At least 256 KB free disk space (or a USB drive)
- At least 16 MB free RAM

## Manual Installation

Without the configurator, with RouterOS commands: the container package, the image (as a file or from the `ghcr.io/timbrs/awg-proxy` registry), veth, the WireGuard interface, environment variables, the container — [docs/en/install.md](docs/en/install.md#manual-installation).

## Getting AWG Parameters

1. Open the **AmneziaVPN** app
2. Select the connection
3. Tap **Share**
4. Choose **Protocol**: AmneziaWG, **Format**: AmneziaWG Format
5. Save the `.conf` file

No AmneziaWG server of your own? The configurator has a **"No AmneziaVPN — Cloudflare WARP"** mode with a free WARP config. What happens when the config lacks some parameters and how WARP mode works — [docs/en/install.md](docs/en/install.md#getting-awg-parameters).

## Server Mode (1:N) — Detailed Setup

One awg-proxy on a VPS or a MikroTik serves many clients: a WireGuard server plus awg-proxy in `server` mode (Docker Compose or systemd), adding clients, direct AmneziaWG clients, [routing between clients](docs/en/server-mode.md#routing-between-clients) — [docs/en/server-mode.md](docs/en/server-mode.md).

## Additional Settings

- [Environment variables](docs/en/env.md) — the full table and a description of each
- [AmneziaWG 3.0, 3.1 and IPv6](docs/en/protocol.md) — what changes on the wire, why S1–S4 ≥ 12, the fallback chain, Happy Eyeballs
- [Routing, DNS and MTU](docs/en/routing.md) — routes through the tunnel, PPPoE and MTU, the limits of DNS routing, address-list routing

## Updating

On RouterOS 7.22+ a container installed from the registry updates with one command, `/system/script/run awg-proxy-1-update` (the configurator installs the script; it also updates nightly at 04:30 and restarts a dead container). By hand — `/container/repull`. Details — [docs/en/update.md](docs/en/update.md).

## Uninstallation

If installed via the configurator:

```routeros
/system/script/run awg-proxy-uninstall
```

The script removes the container, WireGuard interface, NAT rules, routes, environment variables, the update script with its scheduler, restores DNS settings, and deletes itself.

## Troubleshooting

Error messages with fixes are in [docs/en/troubleshooting.md](docs/en/troubleshooting.md):

- [`execvpe /awg-proxy: No such file or directory`](docs/en/troubleshooting.md#execvpe-awg-proxy-no-such-file-or-directory) — the image was not downloaded completely
- [`Site-to-site: handshake did not complete`](docs/en/troubleshooting.md#site-to-site-handshake-did-not-complete)
- [`Storage device not found`](docs/en/troubleshooting.md#storage-device-not-found) — the disk is not formatted as ext4
- [`Insufficient disk space`](docs/en/troubleshooting.md#insufficient-disk-space)
- [`no space to extract layer`](docs/en/troubleshooting.md#no-space-to-extract-layer) — no room to unpack the image, with a RAM workaround
- [`start failed: no default dns servers`](docs/en/troubleshooting.md#start-failed-no-default-dns-servers) — RouterOS 7.24, the container has no DNS set
- [`not allowed by device-mode`](docs/en/troubleshooting.md#not-allowed-by-device-mode)
- [`child spawn failed` / `could not load next layer`](docs/en/troubleshooting.md#child-spawn-failed--could-not-load-next-layer) — routers with 16 MB flash
- [`exited with signal 4 (Illegal instruction)`](docs/en/troubleshooting.md#exited-with-signal-4-illegal-instruction) — hEX refresh / hEX S 2025 / hAP ax S need the armv5 image
- [RU address list not loading after reboot](docs/en/troubleshooting.md#ru-address-list-not-loading-after-reboot)
- [Handshake fails after backup restore](docs/en/troubleshooting.md#handshake-fails-after-backup-restore)

No handshake, no traffic after the handshake, the container keeps restarting — at the top of the same page. Common questions — [FAQ](docs/en/faq.md).

## Building from Source

Requires a C compiler (gcc/musl-gcc), Docker (for container images), and make.

```bash
# Tests
make test

# Local binary build
make build

# Docker images (OCI, for RouterOS 7.21+)
make docker-arm64    # ARM64
make docker-arm      # ARM v7
make docker-armv5    # ARM v5
make docker-amd64    # x86_64
make docker-all      # All architectures

# Docker images (classic format, for RouterOS 7.20 and below)
make docker-arm64-7.20-docker
make docker-arm-7.20-docker
make docker-armv5-7.20-docker
make docker-amd64-7.20-docker
make docker-all-7.20-docker
```

Artifacts are created in the `builds/` directory. Code layout, invariants and how to run the tests are in [AGENTS.md](AGENTS.md).

The configurator has its own jsdom-based checks (dev only — the proxy itself has no dependencies):

```bash
npm install jsdom
node tests/conf3.0-ipv6.test.js
node tests/conf3.0-dns.test.js
node tests/conf3.0-ports.test.js
node tests/conf3.1.test.js
```

### Command-line generator

`tools/awg-gen.js` prints the same RouterOS commands as the configurator, without a browser. It opens `docs/conf3.0.html` in jsdom, fills in the form and presses Generate, so the page stays the only copy of the generation logic and the output matches it byte for byte. It runs locally and offline: the page's network calls are cut off and the private key never leaves the machine. Fit for a person and for an AI agent on the user's machine alike. Needs Node.js 20+.

```bash
npm install jsdom                        # once, in the repository root
node tools/awg-gen.js amnezia.conf > awg.rsc
node tools/awg-gen.js --scenario non-ru --exclude steam,epic amnezia.conf
node tools/awg-gen.js --part routing --services youtube,telegram amnezia.conf
node tools/awg-gen.js --mode site-to-site --endpoint 198.51.100.1 --part side-b
node tools/awg-gen.js --help             # every option, the services and the disks
```

The config is read from a file or from stdin. The script goes to stdout in parts (`# >>> [install]`, `# >>> [routing]`, …) that are run in turn, each after the previous one has finished; `--part` prints one part. The page's notes and errors go to stderr. Exit code 1 means the page refused the config, 2 a bad option.

## License

MIT -- see [LICENSE](LICENSE).
