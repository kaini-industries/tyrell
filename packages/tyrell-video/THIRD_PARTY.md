# Third-party components

Tyrell Video's original code is MIT licensed. LVGL 9.5.0 is linked statically
from the same pinned source archive as Tyrell Status; its MIT license is
included in the package as `lvgl-LICENCE.txt`.

GStreamer, GLib, libdrm and libpulse are dynamically linked system libraries.
GStreamer plug-ins, their codec libraries, Pango, and DejaVu fonts are supplied
as Debian dependencies rather than copied into this package. Their separate
licenses and copyright notices remain in the corresponding installed Debian
packages under `/usr/share/doc/`. Optional SDL2 simulation uses the system SDL2
library. No third-party firmware or media is bundled in the application.

The launcher icon is original geometric artwork created for this project and
is covered by the project MIT license.
