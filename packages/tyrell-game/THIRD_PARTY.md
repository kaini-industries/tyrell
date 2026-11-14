# Third-party components

The framework uses the pinned sources in
[`locks/game-build-inputs.json`](../../locks/game-build-inputs.json). Dependencies
are downloaded into ignored build directories rather than vendored here.

| Component | Version | License | Upstream source |
| --- | --- | --- | --- |
| raylib | 6.2 | Zlib | [raylib 6.2](https://github.com/raysan5/raylib/tree/6.2) |
| ENet | 1.3.18 | MIT | [ENet 1.3.18](https://github.com/lsalzman/enet/tree/v1.3.18) |

Raylib includes additional components with their own notices, including GLFW,
image/font libraries, and the MIT-licensed software renderer. Preserve the
upstream licenses and notices when distributing a linked game. The shared
`tools/make_notices.py` collects upstream notices for native Sandline packages;
those packages include them under `/usr/share/doc/tyrell-tanks/`.

The framework's original code and documentation use Tyrell's MIT license, also
provided in [LICENSE](LICENSE).
