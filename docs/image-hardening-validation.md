# Image workflow hardening and review

Validated November 13, 2026 after the first custom image build. Three fresh
implementation agents worked on lifecycle/provenance, APT freezing, and builder
resources/documentation. Three separate fresh agents adversarially reviewed the
changes and the resulting fixes.

## Changes

- Shared host and guest locks protect package/image work and builder shutdown.
  Active-job checks precede input replacement. Each image job has a distinct
  input directory, durable result, and checksum identity.
- Image status, logs, wait, and export commands recover work after a client
  disconnect. Exports preserve the exact input archive and publish atomically;
  interrupted publication can be retried.
- The portable APT lock retains 1,679 package identities and their exact bytes
  for debootstrap, intermediate stages, kernel downloads, and final export.
  Tyrell Status remains a separate verified package input. Frozen construction
  uses a builder-local repository; normal runtime sources are restored afterward.
- Resource checks reserve internal host space and sum budgets on shared
  filesystems. New builders default to 4 GiB RAM; explicit resizing requires a
  stopped VM. A canonical tool list provisions new builders, and `image-deps`
  supports existing builders.
- Resume validates the retained recipe, completed stage, exact assets, APT lock,
  and earlier-stage sources before moving work. Input-directory relocation is
  allowed; changes to source or dependency identities are rejected. Older work
  without this provenance requires a fresh build.

## Adversarial findings and fixes

The review reproduced and closed three defects: export retries bypassed the
artifact-link publication step; clean builders lacked image tools required by
frozen mode; and compressed APT indexes could disagree with the verified plain
indexes. Status output was also extended with the guest input path referenced
by the capture instructions. Regression coverage includes these failures.

The existing builder exposed Linux sticky-directory protections that blocked
root from opening a user-owned lock directly in `/var/tmp`. Locks now live in
a dedicated private directory. Actual root/user exclusion passed in both
directions.

## Validation scope

The 57 image-workflow regression tests passed. The suites cover lifecycle,
provenance, APT verification and materialization, resource accounting, RAM
controls, provisioning, package/image exclusion, and resume guards. ShellCheck,
shell/Python syntax, JSON, and
documentation links were checked.

Inside the existing ARM64 builder, validation exercised:

- Full frozen-repository capture, materialization, and checksum verification.
- A complete 1,933,107,200-byte frozen image input archive was prepared and
  verified without starting an image build; its job ID, archive checksum, and
  APT lock checksum are retained in `prepared-inputs.json` under local evidence.
- Actual debootstrap downloads using the vendor arguments and only the local
  repository; this was not a full bootstrap installation.
- The pinned kernel download URL and hash, plus isolated APT simulation of
  FactoryTest with the compatible libcamera ABI.
- Image-tool availability without updating or installing packages.
- Short synthetic systemd jobs for success, failure, changed-input rejection,
  guest exclusion, and durable results after transient-unit cleanup.

Small evidence files are local under ignored `build/hardening-validation/`.
Package repository bytes and image input bundles remain on configured image
storage. See [image building](image-building.md) for reproducible commands.

## Limits

No complete image was rebuilt with the hardened workflow, and no custom image
has been physically booted. The original image's checksum and artifact link
were rechecked and remain unchanged. The CardputerZero, its running firmware,
and its SD card were not accessed during this work.
The idle builder VM was stopped after validation; its disk and toolchain remain
available.

The builder toolchain is recorded rather than snapshot-frozen. Exact image
bytes are not claimed reproducible; timestamps and other build state still
vary. Spare-card boot, provisioning, peripheral checks, and recovery remain the
next physical acceptance milestone.
