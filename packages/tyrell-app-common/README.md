# Shared application support

`Tyrell::AppCommands` is a header-only C++17 target for Video and Chess. It owns
command metadata, matching, repeat policy and help/footer formatting. Apps keep
their renderer, state machine, clocks and confirmation dialogs. It introduces no
runtime service, shell protocol or UI framework dependency.

Commands use the app's existing integer key codes. A context table supplies an
action ID, aliases, displayed keys, short label, help, availability, repeat and
confirmation metadata. `find_command` returns disabled matches for app-owned
feedback; check `enabled` before dispatching. Repeated confirmations and ambiguous
bindings are rejected. `command_errors` checks each concrete context's contract.

`help_lines` lists enabled actions by default; callers can include disabled
actions with their reason. `footer` formats chosen enabled actions. Rendering
and fitting that text still use the app's real font metrics and layout tests.
Input adapters must distinguish fresh presses from repeats and prevent held
input from being reinterpreted after a context change.

The independent contract test has no graphics or media dependencies:

```sh
cmake -S packages/tyrell-app-common -B build/app-common -DBUILD_TESTING=ON
cmake --build build/app-common
ctest --test-dir build/app-common --output-on-failure
```

`Tyrell::ShellLink` is an independent, optional static C++17 target. Linking
`Tyrell::AppCommands` does not compile or link transport code. The Linux
transport uses only libc; other platforms provide a disconnected stub. Status
is its experimental first consumer, behind `TYRELL_ENABLE_SHELL_LINK=ON`.

Initialize `ShellLink::from_environment()` before workers/helpers. A canonical
decimal `GH0ST_SHELL_FD` value transfers exclusive ownership of that inherited
descriptor to the library. It must be at least 3, open, connected, AF_UNIX,
SOCK_SEQPACKET and have a same-effective-UID peer. Privilege-boundary launchers
and datagram fallback are outside this Linux pilot. Socket validation cannot
prove that another application object does not own the same descriptor; the
launcher/caller must honor the ownership contract. Validation failure does not
close or change the descriptor. Every path removes the environment variable;
adoption sets FD_CLOEXEC, while per-call MSG_DONTWAIT leaves shared file-status
flags unchanged. Helpers started with exec inherit neither the variable nor the
adopted descriptor. Fork-only children must close inherited descriptors as
usual; initialize before starting any such workers.

The supported protocol is pinned to Gh0st source
[`a52c5d78dbeab6dc7fc8e21604b7bbe05d7a35e5`](https://github.com/therezor/CardPuterZeroOS/tree/a52c5d78dbeab6dc7fc8e21604b7bbe05d7a35e5),
specifically `crates/gh0st-core/src/link.rs`, `crates/gh0st-app/src/run.rs` and
`crates/gh0st-shell/src/run.rs`. It uses one UTF-8 tab-separated message per
packet, at most 512 bytes. The app sends `hello\t1`; receiving `hello\t1` enables
exact, argument-free `pause`, `resume` and `redraw` events. Unsupported hello
versions close the link. Unknown verbs, malformed UTF-8/control characters,
extra arguments, truncated and ancillary packets are discarded. Empty packets
fail closed. Proposed title/theme/content/bar messages are not supported.

`next_event()` reads one packet, with `Ignored` indicating consumed data and
`None` indicating no data. Bound the number of calls per UI iteration and give
shell events priority over keyboard events. `fd()` is pollable. The transport
does not release input, stop drawing or change application lifecycle state.
After doing those things, call `acknowledge_paused()`. Its true return means the
acknowledgment was accepted for sending; it does not guarantee peer delivery.
`service()` makes at most three nonblocking, MSG_NOSIGNAL sends, retaining one
hello, one paused acknowledgment and the latest awake value under backpressure.
`pending_output()` reports retries; `cancel_paused()` removes an unsent obsolete
acknowledgment on resume. Protocol v1 has no sequence IDs, so already-delivered
late acknowledgments cannot be recalled. No worker thread or unbounded queue is
introduced.

`closed()` distinguishes an adopted link that was lost from absent/invalid
configuration. This includes a peer that was already closed at adoption. A
single `Lost` event follows fatal send/receive errors or EOF. Losing the link
does not grant foreground ownership; Status exits without reopening input or
restoring pixels. The shell's roughly 100 ms pause window is not extended by
backpressure. `awake\t1` only inhibits idle dark, and grants no background or
foreground rights. No physical Gh0st compatibility is claimed by these tests.

Transport tests are explicitly enabled, independently of command-only callers:

```sh
cmake -S packages/tyrell-app-common -B build/app-common \
  -DBUILD_TESTING=ON -DTYRELL_APP_COMMON_BUILD_SHELL_LINK_TESTS=ON
cmake --build build/app-common
ctest --test-dir build/app-common --output-on-failure
```

Linux tests use socketpairs and exec helpers to cover validation, message
boundaries, malformed packets, ancillary rights, backpressure/cancellation,
peer loss and descriptor inheritance. On other platforms the same test checks
only the disconnected stub.
