# WAN IP Conflict Fallback Design

## Goal

Prevent false state updates and Teams notifications when router policy routing
causes one WAN probe URL to leave through another WAN. A conflicting result
must fall back to that WAN's remaining URLs. The complete monitoring cycle is
discarded only when no conflict-free combination of successful observations
exists.

## Conflict Rules

An IP candidate for a WAN is rejected when either condition is true:

- It equals the persisted IP of any other WAN. The candidate WAN's own
  persisted IP is excluded from this comparison.
- It equals the candidate selected for another WAN in the current cycle.

Persisted entries are considered even when their WAN is no longer present in
the current configuration. This keeps a newly observed address from silently
claiming an address associated with another WAN in the state file.

## Candidate Collection And Selection

`MonitorService` owns cross-WAN validation because it has access to all current
observations and the complete persisted state. `IpProbe` keeps its current
responsibility and interface: it tries the supplied target URLs in order and
returns the first response that is a valid address within the WAN's configured
networks.

At the start of a cycle, the monitor probes every WAN concurrently with its
complete target, preserving the current fast path. For each successful
observation, the monitor records the source URL and therefore the next URL that
has not yet been tried. If a candidate conflicts, the monitor probes that WAN
again with only its remaining URLs.

Selection uses deterministic backtracking in WAN configuration order. Within
each WAN, candidates retain URL configuration order. This finds a valid
combination when one exists instead of allowing an early greedy choice to
block a later WAN. Candidates already collected during a failed branch are
cached for the remainder of the cycle, so the same URL is not requested twice.

## Cycle Outcomes

When every successfully probed WAN can be assigned a conflict-free candidate,
the existing comparison, notification, and state-save behavior continues with
the selected observations.

When a WAN has no successful response because every URL failed HTTP, parsing,
or configured-network validation, the existing behavior remains unchanged:
that WAN keeps its previous state and does not invalidate other observations.

When a WAN produced at least one valid IP but all of its candidates conflict,
or no conflict-free combination exists across the WANs, the complete cycle is
discarded. The monitor records a structured warning, does not call the
notifier, does not write the state file, and returns an empty change set.

## Error Handling And Logging

The probe adapter retains its current per-URL warnings for request failures,
invalid responses, and addresses outside configured networks. The monitor adds
one structured warning when candidate selection fails. The warning identifies
the affected WAN candidates without changing exception behavior or making a
conflict fatal to the long-running process.

No configuration schema, persisted state format, or third-party dependency is
changed.

## Testing

Monitor service tests cover these behaviors:

- Duplicate first candidates fall back to later URLs and a valid combination
  is saved and notified.
- A candidate equal to another WAN's persisted IP falls back to a later URL.
- Backtracking preserves a valid earlier candidate when only the other WAN has
  a usable alternative.
- When every available candidate conflicts, the complete cycle performs no
  state write and no notification.
- An ordinary all-URL probe failure still preserves prior state without
  invalidating the cycle.

The existing full lint, formatting, type-check, and offline test commands remain
the completion gate.
