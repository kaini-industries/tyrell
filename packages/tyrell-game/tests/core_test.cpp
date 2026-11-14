#include <tyrell/game/fixed_step.hpp>
#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>

void require(bool condition,const char* reason) { if(!condition) throw std::runtime_error(reason); }
int main() {
    try {
        tyrell::game::FixedStepClock clock(.02,3,.2);
        require(clock.advance(.01)==0,"Partial time must not advance simulation");
        require(std::abs(clock.alpha()-.5)<1e-8,"Interpolation must retain fractional time");
        require(clock.advance(.03)==2,"Partitioned frames must accumulate exact simulation time");
        require(clock.alpha()<1e-8,"Whole steps must be consumed");
        require(clock.advance(.115)==3,"Catch-up work must be bounded");
        require(std::abs(clock.alpha()-.75)<1e-8,"Catch-up must retain fractional remainder");
        require(std::abs(clock.dropped_seconds()-.04)<1e-8,"Unsimulated whole steps must be counted");
        auto before=clock.alpha();
        require(clock.advance(-1)==0 && clock.advance(std::numeric_limits<double>::quiet_NaN())==0,
                "Invalid elapsed time must not corrupt the clock");
        require(clock.alpha()==before,"Invalid time must leave residual state intact");
        clock.reset();require(clock.alpha()==0 && clock.dropped_seconds()==0,"Reset must clear state");
        require(clock.advance(1)==3,"Long pauses must not cause a simulation spiral");
        require(std::abs(clock.dropped_seconds()-.94)<1e-8,"Both frame clamp and catch-up drops must be counted");
        bool rejected=false;try {tyrell::game::FixedStepClock bad(0);}catch(const std::invalid_argument&){rejected=true;}
        require(rejected,"Invalid clock configuration must fail early");
        rejected=false;
        try {tyrell::game::FixedStepClock bad(std::numeric_limits<double>::max(),8,std::numeric_limits<double>::max());}
        catch(const std::invalid_argument&){rejected=true;}
        require(rejected,"Overflowing clock configurations must be rejected");
        tyrell::game::FixedStepClock regular;
        int ticks=0;for(int i=0;i<120;++i) ticks+=regular.advance(1.0/120);
        require(ticks==60,"Simulation rate must not depend on render rate");
        std::cout<<"Framework clock tests passed\n";return 0;
    } catch(const std::exception& error) {std::cerr<<error.what()<<'\n';return 1;}
}
