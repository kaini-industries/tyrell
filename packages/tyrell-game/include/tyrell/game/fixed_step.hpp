#pragma once
#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>

namespace tyrell::game {
// Main-thread simulation clock. advance consumes whole steps and retains only
// the fractional remainder for interpolation. Catch-up work is bounded.
class FixedStepClock {
public:
    explicit FixedStepClock(double step_seconds=1.0/60.0, int max_steps=8,
                            double max_frame_seconds=.25)
        :step_(step_seconds),max_frame_(max_frame_seconds),max_steps_(max_steps) {
        if(!std::isfinite(step_) || step_<1e-6 || step_>3600 || !std::isfinite(max_frame_) ||
           max_frame_<=0 || max_frame_>3600 || max_steps_<1 || max_steps_>100000)
            throw std::invalid_argument("Invalid fixed-step clock configuration");
    }
    int advance(double elapsed) {
        if(!std::isfinite(elapsed) || elapsed<0) return 0;
        const double accepted=std::min(elapsed,max_frame_);
        drop(elapsed-accepted);
        accumulator_+=accepted;
        const double available=std::floor((accumulator_+step_*1e-8)/step_);
        const int steps=static_cast<int>(std::min(available,double(max_steps_)));
        if(available>max_steps_) drop((available-max_steps_)*step_);
        accumulator_=std::max(0.0,accumulator_-available*step_);
        return steps;
    }
    void reset() { accumulator_=0;dropped_=0; }
    double step_seconds() const { return step_; }
    double alpha() const { return std::clamp(accumulator_/step_,0.0,1.0); }
    double dropped_seconds() const { return dropped_; }
private:
    double step_,max_frame_,accumulator_=0,dropped_=0;
    int max_steps_;
    void drop(double seconds) {
        const auto limit=std::numeric_limits<double>::max();
        dropped_=seconds>limit-dropped_?limit:dropped_+seconds;
    }
};
} // namespace tyrell::game
