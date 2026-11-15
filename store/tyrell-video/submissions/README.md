# Store submission receipts

These records describe actual Store publication requests. Prepared handoffs and
qualification snapshots remain in their version folders; package bytes are not
rebuilt or changed by submission documentation.

| Version | Submission | Recording and review notes | Status |
| --- | --- | --- | --- |
| 0.1.7-1 | [PR #324](https://github.com/CardputerZero/packages/pull/324) | [Real-device clip and qualification](https://github.com/CardputerZero/packages/pull/324#issuecomment-6890010512) | Automated checks passed; awaiting manual first-app review and merge |

[0.1.7 receipt](0.1.7.json) pins the publisher source, exact public assets and
server SHA-256 digests, PR head, CI results and recording comment. No source URL
was added to the listing. The Store publisher does not change the main project
repository's visibility; the receipt separately records the observed public
visibility. A submitted PR is not yet a live Store catalog entry.
