#pragma once
#include "catalog_model.hpp"
#include <cstdint>
#include <vector>

namespace tyrell::catalog {
// Reads once into bounded memory. The UI never gives an editable cache path
// to LVGL's image decoder; the decoder receives only these verified bytes.
std::vector<std::uint8_t> load_artwork(const Artwork& descriptor);
}
