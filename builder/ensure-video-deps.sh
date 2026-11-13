#!/bin/bash
# Installs only in the dedicated disposable Debian builder, never the handheld.
set -euo pipefail
packages=(
    libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev libdrm-dev libpulse-dev
    gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good
    gstreamer1.0-plugins-bad gstreamer1.0-libav gstreamer1.0-x
    fonts-dejavu-core ffmpeg
)
missing=()
for package in "${packages[@]}"; do
    if ! dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -qx 'install ok installed'; then
        missing+=("$package")
    fi
done
if test "${#missing[@]}" -gt 0; then
    sudo env DEBIAN_FRONTEND=noninteractive apt-get update
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${missing[@]}"
fi
pkg-config --atleast-version=1.26 gstreamer-1.0
pkg-config --exists gstreamer-app-1.0 gstreamer-video-1.0 libdrm libpulse
