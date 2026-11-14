# Third-party components

Dependencies remain outside the source tree in ignored build directories.

| Component | Pinned version | License | Source |
| --- | --- | --- | --- |
| raylib | 6.2 | Zlib | https://github.com/raysan5/raylib/tree/6.2 |
| ENet | 1.3.18 | MIT | https://github.com/lsalzman/enet/tree/v1.3.18 |

Archive identities and SHA-256 values are recorded in
`locks/game-build-inputs.json`. raylib includes GLFW, miniaudio, image/font
libraries, and other dependencies with their original notices in the pinned
source archive. Native Debian packages include the raylib and ENet licenses
plus generated upstream notices under `/usr/share/doc/tyrell-tanks/`, including
the software renderer's separate MIT license. Preserve those notices when
redistributing binaries or sources.
The benchmark includes original geometry, math, EGL, and Linux framebuffer code;
no Retro-Zero source was copied.
