#pragma once

#include <cstdint>
#include <map>

namespace tyrell::video {
// Input adapters preserve physical press/release identity independently of
// renderer events. A held key cannot acquire a new meaning after a UI transition.
class InputEdges {
public:
    void reset() { epochs_.clear(); }
    template<class Dispatch>
    void event(int physical, int logical, int value, std::uint64_t epoch, Dispatch dispatch) {
        if(value == 0) { epochs_.erase(physical); return; }
        if(!logical || (value != 1 && value != 2)) return;
        auto found = epochs_.find(physical);
        if(value == 1 && found == epochs_.end()) {
            epochs_[physical] = epoch;
            dispatch(logical, false);
        } else if(found != epochs_.end() && found->second == epoch) {
            dispatch(logical, true);
        }
    }
private:
    std::map<int, std::uint64_t> epochs_;
};
} // namespace tyrell::video
