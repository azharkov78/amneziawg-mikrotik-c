# AmneziaWG 3.0, 3.1 and IPv6

> Part of the [awg-proxy](../../README_en.md) documentation: a RouterOS container that connects MikroTik to AmneziaWG servers. The easiest way to install it is the [configurator](https://timbrs.github.io/amneziawg-mikrotik-c/conf3.0.html), which generates all commands. Other pages: [install](install.md) · [updating](update.md) · [variables](env.md) · [AWG 3.x and IPv6](protocol.md) · [routing and MTU](routing.md) · [server mode](server-mode.md) · [troubleshooting](troubleshooting.md) · [FAQ](faq.md).

## AmneziaWG 3.0

On the wire, 3.0 differs from 2.0 in exactly one way: the packet header is encrypted with ChaCha20 (RFC 8439) under `AWG_HEADER_PROTECTION_KEY`, using the first 12 bytes of the S padding as the nonce. A transport packet has only its 16-byte header encrypted (`Type` + `Receiver` + `Counter`); handshake init/response/cookie reply are encrypted in full. The padding itself stays in the clear. Everything else in 3.0 (junk, `I1`-`I5`, `S1`-`S4`, `H1`-`H4` ranges) has been supported since version 2.

Two practical rules follow:

- **`AWG_S1`--`AWG_S4` must be >= 12**, otherwise the nonce does not fit in the padding. The proxy refuses to start; `amneziawg-go` enforces the same rule (its README says 8 — that is a typo, the code has `HeaderCipherNonceSize = 12`).
- **Keep `AWG_S4` between 12 and 16.** The outer datagram is IP header + 8 (UDP) + S4 + 16 + `round_up(MTU,16)` + 16. Over IPv4 the header is 20 bytes, so that is `1484 + S4` at the stock MTU of 1420: S4 = 16 gives exactly 1500, S4 = 20 starts fragmenting. **Over IPv6 the header is 40 bytes**, which turns the same numbers into `1504 + S4` — already over the limit at S4 = 0, so there the MTU always has to come down. The ceiling is:

  ```
  IPv4:  floor((1440 - S4) / 16) * 16      # S4=0 -> 1440,  S4=16 -> 1424
  IPv6:  floor((1420 - S4) / 16) * 16      # S4=0 -> 1408,  S4=16 -> 1392
  ```

  This is the same reason `wg-quick` picks 1420 for IPv4 and 1400 for IPv6. The configurator generates S4 in 12--16 and leaves the IPv4 MTU alone; for an IPv6 endpoint, or someone else's `.conf` with a larger S4, it lowers `mtu=` on the WireGuard interface. The proxy itself never changes the MTU — it logs a WARN with the ceiling once the connection runs over IPv6, or when a data packet does not fit the path to the server (see [MTU on narrow links](routing.md#mtu-on-narrow-links-pppoe-lte)).

With no key set, the proxy behaves **byte for byte** like v2 (`hp_off_matches_v2` in the unit tests; the ChaCha20 branch is never entered). `amneziawg-go` works the same way: a zero key means `cipher == nil`.

**What the proxy does not reproduce from 3.0, and why.** The proxy only sees UDP datagrams and holds no session keys — the Noise handshake runs between MikroTik's WG stack and the server. Therefore:

- `ContentPaddingAddition` is added to the plaintext **before** the AEAD; appending bytes from outside would break the tag. The parameter is optional, and without it AWG 3.0 falls back to the standard padding to a multiple of 16 — exactly what MikroTik's WG stack produces. So our profile on the wire is "AWG 3.0 with ContentPaddingAddition disabled", a legitimate configuration rather than a degradation.
- Timings (`RekeyAfterTime`, `RekeyTimeout`, `KeepaliveTimeout`, `MaxHandshakeAttempts`) — the proxy forwards packets as they arrive instead of pacing them. All of these are enforced by the sender only; the server never validates them, so the tunnel works.
- `RejectAfterTime` is a receiver-side policy and cannot be emulated at all. If the server's lower bound is below ~150 s, it will start rejecting packets from a key MikroTik still considers live (rekey starts at 120 s, the old keypair lives until 180 s). The proxy logs a WARN at startup.

All of these variables are accepted so a provider `.conf` carries over intact, and each is logged as not applied. What stays visible to a DPI: packet sizes remain multiples of 16, and the WireGuard rhythm (5 s retries, 10 s keepalive, 120 s rekey) is not disguised.

**Fallback chain (backward compatibility / DPI resilience).** The primary profile (`AWG_S*`, `AWG_H*`, `AWG_I*`, `AWG_HEADER_PROTECTION_KEY`) can be extended to four stages: `AWG_FB_*`, `AWG_FB2_*`, `AWG_FB3_*`. The typical chain is v3 → v2 → v1.5 → v1. The initiator (`normal`) runs on the primary profile and, if the remote stays silent for longer than `AWG_FB_AFTER` seconds, cycles through the remaining stages until one works. The responder (`reverse`) accepts any stage and replies with whichever one the handshake arrived on. In `server` mode the profile is tracked **per client** (a source-address cache plus a field in the session table), so clients on different versions never override each other. A non-zero `AWG_S4` is now allowed: buffer headroom is sized from the largest S4 across all stages.

## AmneziaWG 3.1

3.1 adds two things on top of 3.0, and the proxy implements both.

**`RandomTrailers` (`AWG_RANDOM_TRAILERS=on`)** appends a random tail to every handshake (init, response, cookie reply), so their sizes stop being a fingerprint — an init used to weigh exactly `S1 + 148` bytes, and that single number was enough to spot AmneziaWG in a UDP stream. The tail length is drawn from `[0, window − packet size)`, where the window is the largest datagram seen on this connection (never below 500 bytes, never above 1500), so a padded handshake lands in the same size range as the tunnel's own traffic. On receive, the proxy accepts a handshake of any length from the expected one upwards and cuts the tail off.

Transport packets are left alone: there the trailer goes **inside** the encryption (as ordinary content padding) and is stripped by WireGuard on the far side. The proxy holds no session keys, so appending bytes from outside would break the AEAD tag — and nothing is lost, since transport packets already vary in size; the handshakes were the fingerprint.

The setting has to match the server: a 3.0 peer measures sizes exactly and drops a padded handshake. In the fallback chain the trailer is per stage (`AWG_FB_RANDOM_TRAILERS`), so a stage describing an older server stays byte for byte as it was.

**`DisableCookies` (`AWG_DISABLE_COOKIES=on`)** stops cookie replies (type 3) from going out at all. A cookie is the answer to a handshake under load, and it makes the server easy to probe: send a junk init, get back a packet of a known size. With this on, the proxy drops that reply. It is meaningful in `reverse` and `server` mode, where our own WireGuard emits cookies; in plain client mode they hardly ever occur.

Both are carried over from a `.conf` by the configurator as-is (`RandomTrailers = on` → `AWG_RANDOM_TRAILERS=on`); `on`/`off` and `1`/`0` are both accepted.

## IPv6

IPv6 works on both legs. **Outbound** (`AWG_REMOTE`, container → AWG server) is described below. **Inbound** (`AWG_LISTEN`) is needed in `server` and `reverse` modes, where the socket faces the internet: a hub on a router without a public IPv4 is reachable for clients over IPv6 only. Putting an IPv6 address into `AWG_LISTEN` switches the socket's family:

```
AWG_LISTEN=:51820             # IPv4, as before — an ordinary client tunnel on the veth
AWG_LISTEN=[::]:443           # both families: an IPv4 client arrives v4-mapped
AWG_LISTEN=[2001:db8::1]:443  # this address only
```

The family is chosen once, at startup, and from then on the client address is stored everywhere — in the session table, in the profile cache and in the recv/send batches — in one fixed-length form, so the hot path never branches on the family. On `[::]` `IPV6_V6ONLY` is turned off, so a single hub serves clients of both families from one socket.

On a MikroTik the container gets no IPv6 by itself: the veth needs an address (usually a ULA) and a `gateway6`, and inbound traffic has to be steered to it with a `dst-nat` in `/ipv6 firewall nat`. The configurator does this itself when the "Server is reachable over IPv6" box is ticked.

**When it is used.** The proxy resolves `AWG_REMOTE` on `AF_UNSPEC` and keeps the first A and the first AAAA record. From there:

- **AAAA only** — runs over IPv6, nothing to decide;
- **A only** — exactly as before, nothing changes;
- **both** — the Happy Eyeballs probe (RFC 8305) runs, see below;
- **an IPv6 literal** in `AWG_REMOTE` (`[2001:db8::1]:443`) — straight to IPv6, no lookup and no probe.

**How the family is chosen.** UDP has no handshake, so the only liveness signal is a packet coming back. With both records present the proxy opens two `connect()`ed sockets and:

1. the first outbound packet goes over IPv4 (the family every previous release used — IPv6 only takes the connection once it proves it works);
2. if there is no answer within `AWG_HE_DELAY` (250 ms), **the very same** packet is replayed over IPv6. Replaying is safe: both copies carry the same TAI64N, so a server that received both rejects the second as a replay and answers exactly once;
3. whichever family answers first wins; the loser's socket is closed and behaviour from then on is identical to a single-stack configuration — no steady-state overhead.

The probe waits in `poll()` on the two descriptors and never reads, so the winning datagram is left queued for the normal receive path. A `POLLERR` (ICMP unreachable) on one socket hands the connection to the other family immediately. Every reconnect restarts the probe — and since `dial_remote()` re-resolves the name each time, that doubles as the re-resolve-on-failure path.

**Learned preference (`AWG_STATE_FILE`).** If the site's IPv4 is dead (typical behind CGNAT), the 250 ms head start is paid again on every reconnect. So the probe's outcome is remembered: one byte goes into a file -- `6` or `4` -- and the next start dials the learned family first. The write happens **at most once per process run** and only when the byte on disk is stale: the router's flash is small, and a flapping link must not turn into a write loop.

What the values mean matters. `6` is only an optimisation: skip a head start for an IPv4 known to be dead. `4` is **not** "IPv6 off" but a return to the standard order, in which the IPv6 socket is still opened and probed on every connect and reconnect. So a temporary IPv6 outage is not remembered forever: as soon as IPv4 goes quiet again, IPv6 gets a new chance and can win the preference back. If the name has no AAAA at all (or `AWG_REMOTE` holds an IPv4 literal), IPv6 is not used at all, and no marker changes that.

The file lives in the container's root and survives a restart. `AWG_STATE_FILE=""` turns saving off entirely -- the behaviour goes back to plain RFC 8305 with no memory.

**MTU.** The IPv6 header is 20 bytes longer and a full-size packet no longer fits 1500 — formulas and ceilings are in the AmneziaWG 3.0 section above. The proxy does not change the MTU (it belongs to the router's WireGuard interface); it logs this on connect instead:

```
WARN: set the WireGuard interface MTU to 1408 or lower: remote is IPv6 (header 20 bytes longer than IPv4)
```

The configurator sets `mtu=` itself: the "Server is reachable over IPv6" box is ticked automatically as soon as the endpoint is recognised as an IPv6 literal. For a DNS name the box stays off (the browser deliberately does not resolve your server's name — it would go to a third party), but the generated script asks the router itself:

```
:do {
  :local a6 [:resolve vpn.example.com type=ipv6]
  :if ([:len $a6] > 0) do={ /interface/wireguard/set [find name=wg-awg-proxy-1] mtu=1392 }
} on-error={}
```

The `type=` parameter of `:resolve` is not present in every RouterOS 7.x build; `on-error={}` makes sure the install does not fail without it and the MTU simply stays at its IPv4 value. In that case tick the box by hand or set `mtu=` yourself.

**What is not covered.** The rest of the RouterOS generation (`/ip/address`, `/ip/route`, `/ip/firewall`, veth, policy routing) stays IPv4. For the "all non-RU traffic" scenario this is not a problem: the routing-loop guard is only needed for IPv4 traffic, and with an IPv6 server it is simply not created — the policy routing never reaches it anyway.

**`AWG_NO_DF` and IPv6.** IPv6 has no DF bit — a transit router there never fragments. On an IPv6 socket the option (`IPV6_MTU_DISCOVER=IPV6_PMTUDISC_DONT`) only affects the local stack: it stops honouring PMTU replies. The symmetry is kept so `AWG_NO_DF=1` means the same thing on both families.
