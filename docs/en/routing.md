# Routing, DNS and MTU

> Part of the [awg-proxy](../../README_en.md) documentation: a RouterOS container that connects MikroTik to AmneziaWG servers. The easiest way to install it is the [configurator](https://timbrs.github.io/amneziawg-mikrotik-c/conf3.0.html), which generates all commands. Other pages: [install](install.md) · [updating](update.md) · [variables](env.md) · [AWG 3.x and IPv6](protocol.md) · [routing and MTU](routing.md) · [server mode](server-mode.md) · [troubleshooting](troubleshooting.md) · [FAQ](faq.md).

## Routing Traffic Through the Tunnel

Specific host:

```routeros
/ip/route/add dst-address=8.8.8.8/32 gateway=wg-awg-proxy
```

Subnet:

```routeros
/ip/route/add dst-address=10.0.0.0/8 gateway=wg-awg-proxy
```

View routes:

```routeros
/ip/route/print where gateway=wg-awg-proxy
```

Remove a route:

```routeros
/ip/route/remove [find where dst-address="8.8.8.8/32" gateway="wg-awg-proxy"]
```

## DNS Through the Tunnel

To route DNS queries through the tunnel, set DNS servers and add routes to them:

```routeros
/ip/dns/set servers=8.8.8.8,8.8.4.4
/ip/route/add dst-address=8.8.8.8/32 gateway=wg-awg-proxy
/ip/route/add dst-address=8.8.4.4/32 gateway=wg-awg-proxy
```

## MTU on narrow links (PPPoE, LTE)

The outer UDP datagram is

```
IP (20 for IPv4, 40 for IPv6) + UDP 8 + S4 + WG header 16 + MTU + tag 16
```

At the stock MTU of 1420 and `S4 = 0` that is 1484 bytes — it fits in 1500. Two cases
break this arithmetic:

- **PPPoE.** The link carries 1492, not 1500. With `S4 = 12` 1496 bytes go out, and
  every full-size packet travels as two IP fragments. `change-mss` does not help here:
  it is about TCP, while what overflows is the outer UDP datagram — which hits QUIC too,
  the protocol YouTube and Google Play run on. The configurator has an
  **"Underlay path MTU"** field: set it to 1492 for PPPoE (lower for PPPoE over a VLAN),
  and the tunnel MTU is computed from it.
- **`MTU` in the `.conf` itself.** If the server sent `MTU = 1300`, that value is now
  carried over to the WireGuard interface. The ceiling computed from `S4` can only push it
  lower, never raise it back.

**The proxy tells you the MTU to set.** Packets to the server carry DF (don't fragment). When a link on the way is narrower than the packet, the node in front of it drops the packet and answers with ICMP "fragmentation needed" (IPv6: "packet too big") carrying the size that fits. The kernel remembers this path MTU and from then on fragments larger packets itself: the tunnel works, but throughput drops. Every 5 seconds the proxy asks the kernel for that path MTU and logs:

```
INFO: remote: path MTU to the server is 1492, larger packets are fragmented
WARN: set the WireGuard interface MTU to 1408 or lower: packets to the server are 1496 bytes, the path takes 1492, each one is fragmented
```

The number in `WARN` is the value for the router's WireGuard interface, computed with the formula above from the real path MTU and your `S4`:

```routeros
/interface/wireguard/set [find name=wg-awg-proxy] mtu=1408
```

- `WARN` appears only when data packets do not fit the path. If the log has only the `path MTU` line, the oversized packet was one sent before the handshake (junk or `I1`–`I5`). It is garbage anyway, costs no throughput, and needs no change.
- The kernel forgets the path MTU after 10 minutes and learns it again. The proxy logs the same value once.
- If ICMP is filtered on the way, the kernel never learns the path MTU and oversized packets are simply lost. `WARN` then appears only for packets over 1500 bytes. Find the path MTU by hand: `/ping <server IP> size=1492 do-not-fragment`, lowering `size` until the ping passes. The largest `size` that passes is the path MTU.
- With `AWG_NO_DF=1` over IPv4, nodes on the way fragment silently, without ICMP. There will be no `path MTU` line, and the slowdown stays.

Up to and including 1.4.2 the proxy took this ICMP for a broken connection: the log said `remote read error (Message too large), will reconnect`, the tunnel went quiet for about a second, and the source port changed.

## DNS Routing: What It Can and Cannot Do

Per-service routing works like this: the client resolves a domain **through the router**,
RouterOS puts the returned address into an address-list, and mangle marks connections to
that address. Two limits follow, and they are worth knowing up front:

- **The client has to ask the router for DNS.** A device with DoH, Android Private
  DNS, a hard-coded `8.8.8.8` or simply the address in its own cache makes no query
  the router could see — its traffic goes past the tunnel. To check:
  open the service and look at `/ip/firewall/address-list/print where list=YouTube`.
  An empty list means the problem is DNS, not AWG.
- **Some services connect by IP with no DNS at all.** Telegram (MTProto) connects
  straight to its own ranges, so they are written into the list statically —
  domains alone will not catch it.
- **Exclusions in the "All non-RU traffic through tunnel" mode work the same way.** The
  `action=accept` rule only fires for addresses the router has already put into the
  exclusion list (`Steam`, `EpicGames` and so on). Anything not in the list — another
  CDN, an address from the client's cache, game servers reached by direct IP — goes into
  the tunnel. To check: `/ip/firewall/address-list/print where list=Steam`, and to find
  out where a particular connection goes, `/ip/firewall/connection/print
  where dst-address~"1.2.3."` (the mark `connection-mark=…-non-ru` means it is in the
  tunnel). Connections already open do not move over after the list is edited — they have
  to be dropped: `/ip/firewall/connection/remove [find where connection-mark=awg-proxy-1-non-ru]`.

## Short CDN TTLs

Services behind Cloudflare can have a TTL of a few seconds. The address drops out of the
address-list while the browser still holds the connection, and the next connection
goes past the tunnel. The **"Hold DNS address-list entries past their TTL"** box
sets `/ip/dns address-list-extra-time` (30m by default).

The setting is router-wide, not per list, so:

- it is applied **only** if the router still has the default `0s` — a value someone else
  set is left alone, and the script says so in its output;
- the uninstall script resets it to `0s` **only** if the value is still exactly
  what the configurator set.

Too large a value keeps stale CDN addresses around and can pull unrelated traffic into the
tunnel, so 10–30 minutes is a sensible ceiling.

## Address-List Based Routing (Advanced)

For selective traffic routing through the tunnel, use routing tables and mangle rules.

Create a routing table:

```routeros
/routing/table/add disabled=no fib name=r_to_vpn
```

Default route through the tunnel for this table:

```routeros
/ip/route/add dst-address=0.0.0.0/0 gateway=wg-awg-proxy routing-table=r_to_vpn
```

Address list with destinations to route through the tunnel:

```routeros
/ip/firewall/address-list/add address=8.8.8.8 list=to_vpn
/ip/firewall/address-list/add address=1.1.1.1 list=to_vpn
```

Mangle rules for traffic marking:

```routeros
# Skip local traffic
/ip/firewall/mangle/add chain=prerouting action=accept dst-address=10.0.0.0/8
/ip/firewall/mangle/add chain=prerouting action=accept dst-address=172.16.0.0/12
/ip/firewall/mangle/add chain=prerouting action=accept dst-address=192.168.0.0/16

# Mark connections to addresses in the list
/ip/firewall/mangle/add chain=prerouting action=mark-connection \
    dst-address-list=to_vpn connection-mark=no-mark \
    new-connection-mark=to-vpn-conn passthrough=yes

# Mark routing for tagged connections
/ip/firewall/mangle/add chain=prerouting action=mark-routing \
    connection-mark=to-vpn-conn new-routing-mark=r_to_vpn passthrough=yes
```

NAT for marked traffic:

```routeros
/ip/firewall/nat/add chain=srcnat action=masquerade routing-mark=r_to_vpn
```

Now all traffic to addresses in the `to_vpn` list will go through the tunnel. Add addresses to the list as needed.
