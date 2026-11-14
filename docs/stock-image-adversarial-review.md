# Stock-image workflow adversarial review

Reviewed November 14, 2026 by three fresh agents and the primary implementer.
The review covered release discovery and lock validation, captured inputs and
job lifecycle, privileged image handling, offline installation, provisioning,
preservation evidence, the independent verifier, and README/workflow claims.

## Confirmed defects and fixes

| Priority | Reproduction or impact | Final correction |
| --- | --- | --- |
| P2 | A newer official stock release named for a different Debian release or architecture was filtered out before selection, allowing `check` to keep reporting an older Trixie image as current. | Recognize stock releases broadly, select the newest eligible channel release, and explicitly reject unsupported or incomplete images. Asset-free project tags remain excluded. |
| P2 | A nearly full 64 GiB guest could pass the host backing-volume check and fill its filesystem during bundle transfer, extraction, or raw-image creation. The decompressor also had no exact output bound. | Budget known host download/archive bytes before writing; query actual guest free space before transfer; include both input copies, raw and compressed output, and a reserve; recheck output capacity and cap decompression at the measured XZ raw size. |
| P2 | Missing `mode` fields were accepted in preservation evidence, and ownership was not recorded. Chmod or chown changes could therefore escape the documented identity check. | Require exact type-specific snapshot fields, including mode, UID, and GID; compare them independently against the final image. |
| P2 | A protected subtree represented by a symlink could hide changes to its target because only the link was recorded. | Require real directory leaves for protected subtrees and a regular driver record. The stock `/lib` usr-merge parent link remains supported. |
| P2 | The actual stock image routes three firmware selections through `/etc/alternatives`. Retargeting those links changed active firmware without changing previously protected firmware files. | Include the entire alternatives directory in the protected scope and reject route changes. |
| P2 | Stock verification inherited the source-build exception for an empty non-root staging password, despite stock-derived provisioning promising locked credentials. | Allow that legacy exception only in source mode; stock-derived accounts must have locked password fields. |

Regression cases reproduce the incorrect selection, low-space refusal before
guest writes, overlong/truncated decompression, omitted identity fields,
permission/ownership changes, symlinked roots, firmware route changes, and empty
stock passwords. Cross-module fixtures use the actual overlay and verifier
helpers to check that their evidence formats agree.

## Validation

- The complete image suite passed **114 tests** after the fixes, including the
  original source-build lifecycle/resource/APT/resume regressions.
- Python compilation, shell syntax validation, and `git diff --check` passed.
- The XZ size parser was exercised against the actual pinned official image and
  returned **7,415,529,472 uncompressed bytes** without expanding it.
- The first-build [580-check ARM64 result](stock-image-validation.md) remains a
  historical pre-review result with its original captured code and evidence.
  Its evidence lacks the newly required ownership fields and is deliberately
  rejected by the stricter standalone verifier.
- A fresh post-review VM build was attempted, but startup correctly refused
  when the Mac had **3.80 GiB** available against the existing **4.25 GiB**
  combined internal reserve. That guard was retained. A complete post-review
  ARM64 image rebuild has therefore **not** been run.

No unresolved actionable defect remains from this review. The blocked full
rebuild is a validation limit, and physical cold-boot/peripheral acceptance on
a spare card remains pending. No handheld or SD card was accessed during the
review.

## README and operating workflow

The README now separates `make image-stock` from the frozen source
`make image` path, shows explicit candidate-lock selection, names the host
`xz` requirement, explains stable/prerelease policy and capacity checks, and
links the workflow and both validation records. Normal builds continue to use
their saved lock; they never silently adopt a changing upstream “latest.”
