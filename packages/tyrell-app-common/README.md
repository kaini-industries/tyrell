# Shared application commands

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
