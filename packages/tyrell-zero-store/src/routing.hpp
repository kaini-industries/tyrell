#pragma once
#include <string_view>

namespace tyrell_store {
enum class Route { ui, helper, reject };
inline Route route(bool root, int argc, const char* const* argv) {
    const bool helper = argc > 1 && std::string_view(argv[1]) == "--package-helper";
    if (root) return helper ? Route::helper : Route::reject;
    for (int i = 1; i < argc; ++i)
        if (std::string_view(argv[i]) == "--package-helper") return Route::reject;
    return Route::ui;
}
}
