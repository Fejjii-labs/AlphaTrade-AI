# SFP level approach

`SFP_LEVEL_APPROACH` fires when a live quote is within `approach_proximity`
(default 0.1% on the v2 parameter factory) of a tracked prior high, low,
support, or resistance, and the price has not yet passed the minimum sweep
depth. It is preparation only. The existing sweep and confirmation alerts
stay. SFP execution remains `sfp_execution_plan_not_authorized`.

Stored strategy versions are immutable. This change does not rewrite them.
A spec whose `approach_proximity` is absent or null does not emit the alert.

## How Sofien approves the new version

1. Create a new strategy version with `sfp_research_v2_parameters()`
   (`version` `sfp-research/v2`, lookback 72, sweep depth 0.2–0.3%,
   approach proximity 0.1%). Do not edit the approved v1 row.
2. Review the draft in the existing strategy lifecycle.
3. Approve that new version through the existing human approval path.
4. Point the watch assignment at the approved version id.

Until that approval, the live v1 strategies keep lookback 6 and sweep depth
1%, and they do not emit approach alerts.

Autonomous consumers may read the approach event to prepare an entry. They
must not treat it as execution authority. Both autonomous flags stay off.
