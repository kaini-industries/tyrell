# Shared commands and deterministic UI checks

U1 adds a shared C++17 command model to Video 0.1.8 and Chess 0.1.3 source.
It keeps their LVGL/raylib renderers, M5 launcher entries, package install paths,
media implementation, game rules and LAN protocol. New binaries require their
own device acceptance; earlier qualified packages remain separately retained.

## Command model

`packages/tyrell-app-common/` exposes the header-only `Tyrell::AppCommands`
target. Each app supplies its own typed action IDs and existing integer key
codes. A concrete screen/context defines key aliases, visible keys, label/help,
availability, repeat policy, confirmation metadata and an unavailable reason.
There is no graphics, media, network or shell dependency in this target.

The same definitions feed key lookup, contextual help and footer hints. The
controller retains ownership of effects, confirmation dialogs and unavailable
action feedback. Disabled commands are not advertised as available. Ambiguous
bindings and repeated confirmations are rejected; tests also check each table
against the app's actual context and behavior.

Video's browser, player, menus and error screen have distinct commands. Help
lists the originating context and uses the existing scrollable menu. Chess
uses current board/menu/network state to include valid actions and omit
unavailable hints, undo or replay. Its help is paginated rather than silently
discarding commands beyond the renderer's row limit.

## Input behavior

Existing bindings and aliases remain, including arrows/WASD, Chess keypad
Enter and punctuation navigation. Navigation, seeking, volume and supported
setting changes can repeat. Open, confirm, toggles and exits require fresh
presses. Video's Right/D still opens the selected item, but no longer repeats
that activation while held.

Video tracks each physical press's UI context. A held key cannot become a new
action after opening a file or menu. Release, focus loss and input loss clear
the repeat state; lost events cannot manufacture a fresh activation. Native
tests feed evdev press/release/repeat and `SYN_DROPPED` sequences through the
actual adapter logic.

The optional SDL simulator uses raw SDL key/repeat/release events through a
bounded queue processed on the UI thread. This preserves repeat information
that the pinned LVGL SDL keyboard adapter discards. The SDL-enabled test
injects real SDL events with a dummy backend. The normal native package is
built with SDL disabled and gains no SDL runtime dependency.

Chess retains its context-bound directional repeat logic and ordered key
stream. Held Enter cannot confirm a LAN departure or another protected choice.
Its existing rapid-tap, held-key, keypad and input-resynchronization coverage
remains part of the regression suite.

## Deterministic time and real rendering

Video's app UI clock is injectable for tests of control hiding and transient
notices. Tests advance fixed timestamps rather than sleep. Playback position,
decoder timing and production timing sources remain separate.

Chess accepts a headless-only `--ui-time` for reproducible UI previews; its
controller tests can also fix the UI event clock. This changes notice/animation
display timing, not `nowMs()`, match-clock authority, LAN timeouts, AI budgets or
puzzle scheduling. Production launches keep their normal UI clock.

The tests render through the real engines at 320×170. Video inspects actual
LVGL objects and fitted text; Chess records fitted strings and draw coordinates
from its renderer. Checks cover long filenames, track names, error/network text,
contextual help, confirmation/promotion/castling controls, notice deadlines and
reduced motion. Essential action hints must remain readable. Long optional text
is bounded or ellipsized, and single-line slots cannot introduce extra lines.

Representative PPM/PNG frames and bounds JSON are generated under each build's
`u1-previews/` directory. They are review artifacts rather than pixel-for-pixel
goldens. These checks do not measure physical LCD scanout or certify key hardware.

## Build and evidence integration

Video's isolated source bundle and Chess's package snapshot include the exact
shared command sources. Tests reject missing or redirected shared inputs and
keep credentials outside the transfer boundary. The common source is included
in both apps' compatibility-ledger source digests; it does not enter Status,
Sandline or Beacon's source boundary.

The [scoped CI runner](continuous-integration.md) requires:

- `tyrell-app-commands` in both consuming app builds.
- `chess-command-context` and `chess-ui-layout` with the existing Chess suite.
- `video-ui-commands` with the existing Video media/render tests.
- A separate SDL-enabled Video UI check using dummy SDL drivers; this build is
  not packaged and does not repeat the full media suite.

CI retains preview/bounds files with its existing source identity, test results,
toolchain inventory and artifact hash mapping. Required skips or missing test
groups remain failures. Native app builds are automated evidence, not physical
M5/Zero/Gh0st acceptance. No optional Gh0st lifecycle support is introduced here.

The approved Store catalog, retained app packages, Zero locks and accepted Z3
image/control captures are unchanged. Current `zero check` correctly reports
`build_ready: false`: changes to the shared transfer tooling and its tests make
the old implementation qualification stale for a new capture. It must be
requalified through the existing pipeline before preparing a new base image;
old receipts are not rebound to changed code. F2 can still use the original
captured baseline/control artifacts for their intended acceptance experiment.

See [compatibility boundaries](compatibility-matrix.md), the
[adoption plan](gh0st-adoption-plan.md) and each app's README for commands and
remaining version-specific qualification.
