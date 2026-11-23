# Chess 0.1.2 clock-boundary review

Reviewed November 23, 2026. This is a source update; the retained ARM64 packages
remain at their original versions and checksums.

## Corrections

Accepting a draw just after a clock deadline previously chose the draw result
before settling the clock. The saved result could then disagree with the flag
state, causing a later load to reject the save. `Game::agreeDraw()` now settles
the supplied timestamp first and preserves a timeout or existing final result.
Local confirmation, host confirmation and incoming TCP acceptance use it.

A local move submitted across the same deadline could discover timeout while
being rejected, without invoking normal game completion. Selection or promotion
could remain open and the completed game would not be archived. Rejected moves
that find a terminal result now run completion cleanup and checked persistence.

## Verification

The updated headless source rebuilt successfully and all **17 Chess CTest
groups passed**, including real loopback TCP tests. Added regressions cover:

- Draw acceptance before, exactly at and after the deadline; saves reload and
  repeated acceptance preserves the finished result.
- Local and host draw confirmations plus a TCP acceptance arriving after expiry.
- Promotion submitted after expiry, with no added move, cleared UI state,
  a reloadable active save and a completed-game archive.

The existing framework and Tanks source had already passed 6 and 12 host CTest
groups respectively; Tanks includes framework groups. Four packaging-tool
regressions passed. No ARM64 package build or device access occurred for 0.1.2.

## Retained release evidence

Commit `5e2d717` preserves the imported 0.1.1 source snapshot. All **103 project
source hashes** in its retained manifest match that commit. Both retained
0.1.0 and 0.1.1 distribution directories passed their seven checksum entries.
Their packages, manifests and test logs remain unchanged.

The [0.1.1 device validation](chess-ui-input-validation.md) applies to that
retained package. Version 0.1.2 still needs ARM64 package and device qualification.
