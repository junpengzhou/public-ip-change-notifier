# WAN Public Network Validation Design

## Goal

Prevent false public IP change notifications when router policy routing is not
ready or is temporarily polluted after a restart. Each WAN must declare its
allowed public IP networks. A probe result outside those networks is treated as
a failed observation and must not replace persisted state or trigger a
notification.

## Configuration

Replace each `servers` URL list with a mapping containing `networks` and `urls`:

```yaml
servers:
  wan1:
    networks:
      - "218.0.0.0/8"
    urls:
      - "https://ifconfig.me/ip"
      - "https://ipinfo.io/ip"
```

Both lists are required and must contain at least one item. Networks use
standard IPv4 or IPv6 CIDR notation and are validated at startup. The new shape
is intentionally strict: the old URL-only list is rejected so a WAN cannot run
without the protection requested by this change.

## Components And Data Flow

The configuration layer exposes one typed WAN configuration containing parsed
networks and validated URLs. The CLI converts those values to the standard
library network objects and strings expected by the probe adapter.

For each WAN, the probe adapter tries URLs in configured order. A response is
successful only when it is a valid IP address and belongs to at least one of
that WAN's configured networks. An address-family mismatch is simply a network
mismatch. A matching address produces the existing successful observation.

If a URL returns an address outside every allowed network, the adapter records
a `probe_ip_outside_allowed_networks` structured warning and tries the next
URL. If every URL fails or returns a disallowed address, the adapter returns an
empty observation. The monitor then follows its existing failure behavior: it
retains the previous IP, writes no false change, and sends no notification.

## Error Handling

Malformed or missing CIDRs, empty network lists, empty URL lists, and blank WAN
names fail configuration validation before monitoring starts. HTTP failures,
invalid response bodies, and disallowed IPs remain per-URL probe failures so
one bad endpoint does not prevent fallback to later endpoints.

No new third-party dependencies are required. CIDR parsing and membership use
Python's `ipaddress` module.

## Testing

Configuration tests cover the new shape, IPv4 and IPv6 CIDR parsing, missing or
empty lists, invalid CIDRs, and rejection of the legacy URL-only shape. Probe
tests cover an allowed address, fallback after a disallowed address, complete
failure when all addresses are outside the allowed networks, and IPv4/IPv6
family mismatch. Existing monitor tests continue to prove that an empty
observation preserves prior state and suppresses notifications.

README and sample configuration are updated to document the required CIDR
format and the behavior of rejected observations.
