# Tyrell game framework architecture

The framework lives in `packages/tyrell-game/` and exposes namespaced C++17
headers under `tyrell/game/`. It is part of the same repository and dependency
workflow as Tyrell's applications. Sandline consumes it instead of keeping a
private copy of platform and transport code. Beacon Arena supplies a second,
tank-independent consumer.

## Dependency direction

```text
Sandline simulation / protocol / UI      Beacon simulation / protocol / UI
                  \                         /
        runtime + render + network + core public modules
                       |
             raylib / ENet / OS interfaces
```

Public framework headers and implementation do not include Sandline's
`world.hpp` or know its snapshot layout. CMake targets separate the independent
core and socket-only networking module from rendering dependencies.

| Framework owns | A game owns |
| --- | --- |
| Graphics context, display buffers, input, frame pacing, cleanup | What to draw and which controls mean movement/actions |
| Bounded simulation-step accumulation | State, movement, collisions, health, scoring and other game rules |
| Primitive flat lighting and scoped camera/transforms | Camera choice, art, UI, models and map geometry |
| ENet socket/packet lifecycle, channels and opaque peer IDs | Version handshake, peer-to-player assignment and authority |
| Network-byte-order scalar codecs and sequence comparison | Packet schema, field ranges, snapshot acceptance and timeouts |

This keeps the extraction useful without making a generic engine from
Sandline-specific assumptions.

## Runtime contract

Construct `tyrell::game::Runtime` with `RuntimeOptions` once. The build chooses
desktop, native, or memory rendering; `Backend::Default` uses that choice.
`poll()` begins an input/update cycle. Use `elapsed()` with a fixed-step clock,
read held/pressed/released keys, and drain `character()` for text input. Render
between `begin_frame()` and `end_frame()`. Screenshots export the logical
rendered image rather than a desktop screenshot containing unrelated windows.
Only one runtime/context is allowed. Pacing accepts zero (unpaced) or 1–1000
frames per second. Begin camera passes outside all transform scopes. Camera
passes cannot nest; transform scopes may nest up to 30 levels. Both scopes must
end before `end_frame()`. Raw rlgl stack operations remain the caller's
responsibility. Invalid/singular camera projections throw, and a finite
`world_to_screen` result alone does not establish visibility.

The native path discovers the LCD and keyboard, checks framebuffer geometry,
keeps held keys stable between frames, and resynchronizes evdev state after
`SYN_DROPPED`. Each completed frame is converted to RGB565 and committed once,
avoiding the known deferred-I/O scheduling bottleneck. Ordinary exit and handled
signals restore display state and descriptors. Sudden power loss or SIGKILL
cannot run cleanup. A runtime does not install services or replace the launcher.

Use the stock APPLaunch handoff for a packaged native game. A standalone sample
does not arbitrate an already-running display owner. Memory rendering opens no
device nodes and is the appropriate automated-test path. Actual physical key
rollover, panel tearing, thermals, and battery life require device validation.

`FixedStepClock::advance(elapsed)` returns a bounded number of whole simulation
steps and retains a fractional remainder exposed by `alpha()`. It drops excess
catch-up work rather than causing an unbounded update loop; dropped time is
reported by `dropped_seconds()`. Negative/nonfinite elapsed values add no time.
Consumers choose the simulation frequency and decide how to use interpolation.

## Networking contract

`tyrell::game::net::Transport` owns ENet and presents copied events. Public
`PeerId` values are opaque and remain distinct after reopening a transport.
Opening a host or connecting replaces the previous endpoint. Poll, send,
flush, and close are main-thread operations; do not use one transport from
multiple threads.
Event polling and sends are nonblocking; hostname resolution in `connect()` is
synchronous. Numeric LAN addresses avoid a DNS lookup during connection setup.

The defaults expose three independently sequenced channels:

| Channel | Suggested traffic |
| --- | --- |
| `kControlChannel` (0) | Reliable handshake and important control events |
| `kSnapshotChannel` (1) | Unreliable sequenced world snapshots |
| `kInputChannel` (2) | Unreliable sequenced input commands |

Reliability is selected explicitly by the `send` argument. Channel names do not
automatically make messages reliable. Packet and receive-queue bounds protect
the update loop from unbounded allocation. Transport timeouts handle peer
liveness; games should also bound handshake/input/snapshot age according to
their simulation needs.

`ByteWriter` encodes explicit big-endian `u8/u16/u32/f32` scalars, has a byte
limit, and latches failure on overflow/nonfinite floats. `ByteReader` borrows
input bytes and latches truncated/nonfinite reads. Validate a complete candidate
message—including `finished()` and all game-specific ranges—before committing
state. Native C++ struct layout is never a wire format.

## Independent example

Beacon Arena uses its own `TYBC` magic, schema version, four beacon slots,
movement bounds, pulse state, and 64-byte snapshots. The host simulates at 60 Hz,
publishes at 20 Hz, expires stale controls, and accepts bounded client inputs.
Clients send inputs at 30 Hz and stop playing stale state after three seconds.
Only the host assigns actor positions. Its simple rendering uses the shared
runtime, geometry, transforms, and camera helpers.

The example deliberately omits client prediction, interpolation, collision,
round rules, discovery, and authentication. Sandline keeps its own gameplay
and snapshot adapter while reusing the same lower-level transport. The two
schemas are incompatible by design and use separate default ports.

Refer to [the package README](../packages/tyrell-game/README.md) for commands,
embedding, configuration switches, and known limits. Framework extraction does
not install or replace the already tested game on a physical Zero; any new
device binary needs its own build and hardware acceptance record.
The [validation record](game-framework-validation.md) lists the extraction's
build matrix and consumer checks.
