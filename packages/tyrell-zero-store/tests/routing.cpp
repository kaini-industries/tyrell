#include "routing.hpp"
#include <cassert>
int main() {
    using namespace tyrell_store;
    const char* ui[] = {"untrusted-argv-zero"};
    const char* install[] = {"x", "--install", "anything"};
    const char* helper[] = {"x", "--package-helper", "install"};
    const char* misplaced[] = {"x", "--backend", "--package-helper"};
    assert(route(false, 1, ui) == Route::ui);
    assert(route(true, 1, ui) == Route::reject);
    assert(route(true, 3, install) == Route::reject);
    assert(route(true, 3, helper) == Route::helper);
    assert(route(false, 3, helper) == Route::reject);
    assert(route(false, 3, misplaced) == Route::reject);
    assert(route(true, 3, misplaced) == Route::reject);
}
