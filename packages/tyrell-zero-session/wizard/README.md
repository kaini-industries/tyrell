# Private native Wizard integration

This builds a derivative of the real M5 LaunchWizard GUI, rather than replacing
its screens with a stub. Apply the patch to an isolated vendor source stage:

```sh
python3 packages/tyrell-zero-session/wizard/apply-patches.py /path/to/vendor/source
python3 packages/tyrell-zero-session/wizard/native_config.py /path/to/vendor/source
cd /path/to/vendor/source/projects/LaunchWizard
CONFIG_DEFAULT_FILE=tyrell_native_config.mk scons -j2
```

Use `builder/zero-vendor.py` to materialize the pinned offline SDK/dependencies
and the existing Linux ARM64 builder to run SCons. The patch helper accepts only
the three exact input hashes from launcher commit
`fd71a2dc2e30737cadebd47fb0a2ba68406ecfb8`, validates the patch hash, and checks
all output hashes. A completely patched stage is idempotent; unknown or partial
stages require restaging. It does not modify the vendor checkout or hardware.

The native configuration derives from the pinned Cardputer runtime defaults
with only the cross compiler prefix removed. Generic native defaults omit the
framebuffer backend. The generated filename avoids the vendor's `cross` branch,
and retains framebuffer FULL rendering, evdev, pthread, NEON and FreeType.

The private executable belongs at `/usr/libexec/tyrell-zero/LaunchWizard`, with
`WorkingDirectory=/usr/share/APPLaunch` and the original vendor resources and
font lookup. The patch introduces no UI/font changes; the existing Wizard uses
fonts of at least 12px. The installed vendor Wizard and launcher remain intact.

Install `completion.py` as `/usr/lib/tyrell-zero/session/wizard_complete.py` and
the executable `tyrell-zero-wizard-complete` wrapper as
`/usr/libexec/tyrell-zero-wizard-complete`. The wrapper uses Python isolated mode
and a fixed module path. The CLI runs as root only, with fixed commands and no
alternate filesystem root or environment-based policy overrides.

Both normal apply and the Imager/preprovisioned shortcut converge on this native
transaction. It never enables desktop autologin, changes the system target,
starts APPLaunch, enables its legacy user service, or disables a vendor unit.
The image profile owns service activation. The current boot remains latched in
setup until a controlled reboot; only the next boot may select native startup.

Normal apply durably begins a transaction before vendor changes, then replays
all confirmed vendor timezone/hostname/network/SSH/account actions. The vendor
account migration journal and keyboard-guide implementation remain unchanged.
No provisioning credentials enter the native checkpoint, native account record,
readiness record, command arguments to this helper, or diagnostic output.

The November 19 vendor Wizard adds one-shot root filesystem expansion. The
derivative retains its attempt marker and expansion helper, including the
best-effort failure policy, before native readiness is committed. An Imager
shortcut can schedule the same expansion only while the factory marker exists.
The private Wizard unit requires and orders itself after the vendor's one-shot
`cardputerzero-diskid.service`, so disk identity and partition expansion cannot
write concurrently. Disk-ID failure prevents Wizard startup; the vendor's
already-stamped condition skip remains valid. The vendor unit stays unchanged.
The vendor shortcut's independent marker removal and reboot are removed:
native finalize remains the single owner of those operations. An interrupted
or failed expansion consumes the attempt as in the vendor policy; setup replay
does not rewrite the partition table again. Physical expansion is unqualified.

The outer checkpoint is `/var/lib/tyrell-zero/setup-apply.v1.json`, schema 1,
profile `tyrell-zero`, workflow `native-setup-v1`, with a named step, completion
count, target name and SHA-256. Early hashes identify only the replay target;
they never authorize skipping vendor configuration. After native configuration,
the hash binds the exact account record. A partial migration is resumed by the
vendor journal; an interruption before the account checkpoint returns to the
GUI and requires explicit replay, even when the username already matches.
Legacy numeric checkpoints, incompatible native records, stale identities,
unfinished guides and migrations are preserved and rejected where appropriate.

After account validation, the helper writes native account configuration, uses
the shared state API to commit readiness, writes its final reboot checkpoint,
durably removes setup markers, and removes the checkpoint last. It requests
`systemctl reboot` only after validating the resulting readiness. A failed
reboot leaves durable readiness and the current setup latch, and can be retried.
The Imager route shares these operations and cannot bypass an earlier incomplete
interactive transaction. Missing factory metadata cannot classify baked
factory credentials as an Imager-provisioned account.

Run host-only transaction and patch checks with:

```sh
python3 -m unittest discover -s packages/tyrell-zero-session/wizard/tests -v
```

The tests interrupt every durable write/cleanup boundary, exercise Imager and
normal completion, and check legacy-state, account, boot-latch, guide, journal,
and reboot failures. The actual patched expansion functions are compiled with
temporary marker paths and a fake command runner to check attempt-before-command,
failure/replay and settings rearm without partition or service operations.
They use isolated filesystem and reboot fixtures. They do
not qualify hardware, audio, display, network configuration or actual power
loss behavior; the native ARM64 build and revision 3 image/spare-card acceptance
gates remain necessary.

`tests/fixtures/wizard_service.cpp`, `wizard_service.h` and `main.cpp` are exact MIT
licensed M5 source files from the pinned commit, retained solely for deterministic
offline patch tests. Their original SPDX copyright/license headers are preserved.
