# Guarded power-policy rehearsal

H2 offline increment, November 24, 2026. The tools under `scripts/power/`
prepare policy intentions and rehearse transaction events in memory. They do
not apply a preset, create a tmpfiles override, install a unit or write a
hardware control. Physical Z4/Z5 acceptance and Z6 functional/battery trials
remain pending.

The installed [power-policy foundation](../packages/tyrell-zero-power-policy/README.md),
its version, contract and selected image inputs are unchanged. Its baseline
apply remains a no-op and optimized apply remains closed. A successful
rehearsal cannot authorize that helper or promote a package/image.

## Inspect a proposed change

From the repository root, choose new output paths:

```sh
python3 scripts/power/run.py template --output /private/tmp/h2-snapshot.json
python3 scripts/power/run.py plan --snapshot /private/tmp/h2-snapshot.json \
  --preset combined --generation 0123456789abcdef0123456789abcdef \
  --output /private/tmp/h2-plan.json
```

The template is an **unverified observation**, so it cannot become a ready
simulation or live authorization by filling missing readings. The planner
reports the unresolved identity/eligibility gates. Use the original template
and retain rejected outputs when inspecting those gates; do not relabel a
real observation as a synthetic test to obtain a ready result.

Only explicitly synthetic fixtures can become ready for the memory model.
Their identities and controls are assumptions selected by a test. The
`synthetic-fixture` marker is a declaration, not authenticated hardware proof.
Even a ready fixture has no live execution backend. F2 collector bundles are
not directly accepted as authorization: their source hashes and raw readings
cannot attest the complete running board/controller, installed files or Z4/Z5
acceptance.

The four proposed presets retain the captured values:

| Preset | Ethernet leaf | Delay | Ondemand factor |
| --- | --- | --- | --- |
| baseline | on | 2,000 ms | 50 |
| ethernet | auto | 2,000 ms | 50 |
| cpu | on | 2,000 ms | 1 |
| combined | auto | 2,000 ms | 1 |

All other CPU tunables, 600–1,000 MHz limits, parent USB policy and existing
GPU/CMA/swap, Wi-Fi and EEE settings remain separate preserved requirements.
USB IDs alone cannot identify the integrated Ethernet. A future observer must
prove the reviewed board/controller/port ancestry and both device/interface
drivers without choosing an observed bus number as its identity.

## Rehearse transaction boundaries

An event file is a JSON array. Every event names its type and current 32-digit
hexadecimal generation. For a ready synthetic combined fixture, this example
prepares the generation, advances individual intents, revokes it and restores
the model's journaled values:

```json
[
  {"type":"apply","generation":"0123456789abcdef0123456789abcdef"},
  {"type":"step","generation":"0123456789abcdef0123456789abcdef"},
  {"type":"step","generation":"0123456789abcdef0123456789abcdef"},
  {"type":"invalidate","generation":"0123456789abcdef0123456789abcdef"},
  {"type":"recover","generation":"0123456789abcdef0123456789abcdef"},
  {"type":"recover","generation":"0123456789abcdef0123456789abcdef"}
]
```

Save that array separately, then inspect a new report:

```sh
python3 scripts/power/run.py rehearse --snapshot /path/to/synthetic-fixture.json \
  --preset combined --generation 0123456789abcdef0123456789abcdef \
  --events /path/to/events.json --output /private/tmp/h2-rehearsal.json
```

Event semantics are deliberately bounded:

- `apply` prepares model authorization; each `step` advances at most one
  simulated change. Optional `readback: "fail"` exercises failed verification.
- `invalidate` and `maintenance` revoke the generation before restoration.
  Each `recover` advances at most one owned journal entry; unknown identity or
  conflicting values require recovery rather than overwriting another owner.
- `crash` interrupts and revokes. `restart` does not resume an old apply.
- `snapshot` supplies a replacement `snapshot` object; `control-change`
  supplies a `path` and `value` to model concurrent observations/interference.
- `select` supplies `preset` and `new_generation`. It requires the model's
  baseline-observed phase and a fresh generation.

These are model events, not root verbs. Reports retain errors, transitions and
the input identities. They bind the initial preset/generation, canonical
snapshot/event hashes, exact input-file byte hashes and implementation hashes.
Output files are created exclusively with private permissions. Their scope is
`offline-model-only`, with zero hardware writes and no certified live recovery.
Inputs are bounded to 256 KiB each, snapshots to 64 KiB of canonical JSON and
16 USB nodes, scripts to 64 events, and reports to 2 MiB. Exit status 2 means a
blocked or incomplete rehearsal, interruption or required recovery; its report
remains inspectable. Invalid inputs or output errors return 1. Status 0 describes
a ready plan or completed synthetic scenario, never hardware authorization.
Run `python3 -m unittest discover -s scripts/power/tests -v` for the synthetic
eligibility and failure-boundary checks. Fixture success does not prove a
systemd ordering, kernel deadline, cable wake or electrical outcome.

## Declare physical trials before execution

[power-trial-template.json](power-trial-template.json) is a planning template,
not collected evidence or an executable experiment. Copy it to a new private
record and fill all `UNRECORDED` fields before proposing physical execution.
Keep raw samples/instrument records and their hashes with each attempt, including
failed or excluded trials. The template provides four independent pairs per
candidate, balancing order in the initial schedule; at least three independent
paired trials remain the minimum. Samples within a run are not independent pairs.

Qualify Ethernet and CPU function/performance individually before evaluating
the combination. The combination needs its own function/performance and battery
evidence; isolated effects do not add automatically. Its experiment may proceed
after the individual function/performance gates even when their battery results
are inconclusive. Preserve the baseline fallback.

The [battery protocol](tyrell-zero-battery-validation.md) defines equal elapsed
idle windows, equal useful work and common return-to-idle windows, instrumentation
burden, validated units/uncertainty, unchanged useful endpoints and representative
maintenance exposure. Charging current, USB-only screening, raw `power_avg`, IRQ
counts and frequency residency cannot establish a battery saving. Do not change
gauge calibration, maintenance or shutdown thresholds to produce a result.

## Remaining live implementation and qualification

A future version needs a bounded observer and complete, freshly validated
board/kernel/BSP, CPU-policy, vendor-policy and integrated topology identities.
Eligibility must precede every optimized write and be rechecked after supported
maintenance, policy recreation and hotplug. Preserve administrator overrides;
unknown or conflicting configuration must fail closed.

The CPU path must preserve the exact vendor tmpfiles entries and change only
the selected factor. Before exposing an owned runtime override, prove early
boot and late-policy ordering. The supported tmpfiles reader, apply and
maintenance paths must join the same lock/generation protocol; deleting an
override cannot revoke a reader that already opened it. Event work must be
coalesced and bounded, with no periodic tuning watcher.

Live invalidation must remove only an exact owned artifact and restore affected
live controls only while their identity remains validated. Otherwise retain an
explicit recovery-required state and require controlled baseline boot plus
effective readback. A memory journal cannot certify that recovery.

Before shipment, version changed package/BSP inputs separately, qualify their
source and signed package/base closure, build fresh immutable image inputs and
receipts, and repeat offline and physical acceptance. Preserve the original
M5 binaries, cards and Z3 comparison artifacts; do not rebind historical hashes
to make new power machinery appear accepted.
