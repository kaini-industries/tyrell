#include <tyrell/game/runtime.hpp>
#include <tyrell/game/render.hpp>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <iostream>
#include <limits>
#include <memory>
#include <stdexcept>
#include <vector>
#include "rlgl.h"

namespace {
void require(bool okay,const char* reason) {if(!okay) throw std::runtime_error(reason);}
struct ImageFile {
    std::filesystem::path path=std::filesystem::temp_directory_path()/
        ("tyrell-render-"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count())+".png");
    ~ImageFile() {std::error_code error;std::filesystem::remove(path,error);}
};
bool equal(Color a,Color b) {return a.r==b.r&&a.g==b.g&&a.b==b.b;}
}
int main() {
    try {
        using namespace tyrell::game;
        RuntimeOptions options;options.backend=Backend::Headless;options.width=96;options.height=64;options.fps=0;
        Runtime runtime(options);ImageFile file;
        Camera3D camera{{0,4,4},{0,0,0},{0,1,0},5,CAMERA_ORTHOGRAPHIC};
        const auto center=world_to_screen({0,0,0},camera,96,64);
        require(std::abs(center.x-48)<.001f&&std::abs(center.y-32)<.001f,"Projection must use logical dimensions");
        runtime.poll();runtime.begin_frame();ClearBackground({20,30,40,255});
        {
            CameraPass pass(camera);
            bool rejected=false;try {CameraPass nested(camera);}catch(const std::logic_error&){rejected=true;}
            require(rejected,"Nested camera passes must not corrupt the outer camera");
            require(draw_box({0,0,0},{2,2,2},{100,150,200,255}),"Valid cube must render");
            require(!draw_box({0,0,0},{-1,1,1},WHITE),"Negative geometry must be rejected");
            require(!draw_box({std::numeric_limits<float>::quiet_NaN(),0,0},{1,1,1},WHITE),"Nonfinite geometry must be rejected");
            TransformScope huge_angle({0,0,0},std::numeric_limits<float>::max());
        }
        runtime.end_frame();runtime.screenshot(file.path.string());
        Image image=LoadImage(file.path.string().c_str());require(IsImageValid(image),"Screenshot must load");
        Color* pixels=LoadImageColors(image);int top=0,front=0;
        for(int i=0;i<image.width*image.height;++i) {
            top+=equal(pixels[i],{100,150,200,255});
            front+=equal(pixels[i],shade_color({100,150,200,255},.78f));
        }
        UnloadImageColors(pixels);UnloadImage(image);
        require(top>20&&front>20,"3D faces must retain fixed lighting in the software renderer");
        // A farther object drawn last must remain occluded at the same screen
        // position; this exercises the actual depth buffer, not only API calls.
        camera.position={0,0,5};runtime.poll();runtime.begin_frame();ClearBackground(BLACK);
        {CameraPass pass(camera);draw_box({0,0,0},{2,2,1},BLUE);draw_box({0,0,-2},{2,2,1},RED);}
        runtime.end_frame();runtime.screenshot(file.path.string());
        image=LoadImage(file.path.string().c_str());pixels=LoadImageColors(image);
        require(equal(pixels[32*96+48],shade_color(BLUE,.78f)),"Near cube must occlude the farther cube");
        UnloadImageColors(pixels);UnloadImage(image);
        for(int invalid=0;invalid<6;++invalid) {
            auto bad=camera;
            if(invalid==0) bad.up={0,0,1};
            if(invalid==1) {bad.projection=CAMERA_PERSPECTIVE;bad.fovy=180;}
            if(invalid==2) bad.target=bad.position;
            if(invalid==3) {bad.position={0,0,0};bad.target={0,0,-1e-30f};}
            if(invalid==4) bad.up={0,1e-30f,0};
            if(invalid==5) {
                const float huge=std::numeric_limits<float>::max();
                bad.position={huge,huge,0};bad.target={huge,huge,-1};bad.up={1,-1,0};
            }
            bool rejected=false;try {world_to_screen({0,0,0},bad,96,64);}catch(const std::invalid_argument&){rejected=true;}
            require(rejected,"Invalid camera geometry must fail before producing a projection");
        }
        camera.projection=CAMERA_PERSPECTIVE;camera.fovy=60;
        bool rejected=false;try {world_to_screen(camera.position,camera,96,64);}catch(const std::invalid_argument&){rejected=true;}
        require(rejected,"Projection at the camera eye must reject nonfinite screen coordinates");
        runtime.poll();runtime.begin_frame();
        const Matrix before_projection=rlGetMatrixProjection();
        for(int projection:{CAMERA_ORTHOGRAPHIC,CAMERA_PERSPECTIVE}) {
            auto bad=camera;bad.projection=projection;bad.fovy=std::numeric_limits<float>::denorm_min();
            bool forbidden=false,finite_projection=true;
            try {
                CameraPass pass(bad);
                const Matrix actual=rlGetMatrixProjection();
                finite_projection=std::isfinite(actual.m0)&&std::isfinite(actual.m5)&&std::isfinite(actual.m10);
            } catch(const std::invalid_argument&){forbidden=true;}
            require(finite_projection,"Accepted camera produced a nonfinite projection matrix");
            require(forbidden,"Positive denormal field of view must not create an infinite projection");
            const Matrix after_projection=rlGetMatrixProjection();
            require(after_projection.m0==before_projection.m0 && after_projection.m5==before_projection.m5,
                    "Rejected camera must preserve the projection matrix");
        }
        const double near_plane=rlGetCullDistanceNear(),far_plane=rlGetCullDistanceFar();
        rlSetClipPlanes(1,1);
        bool invalid_planes=false;try {CameraPass pass(camera);}catch(const std::invalid_argument&){invalid_planes=true;}
        rlSetClipPlanes(near_plane,far_plane);
        require(invalid_planes,"Equal clipping planes must not create an infinite projection");
        runtime.end_frame();
        {
            auto bad=camera;bad.projection=CAMERA_ORTHOGRAPHIC;bad.fovy=std::numeric_limits<float>::max();
            bool forbidden=false;
            try {world_to_screen({0,0,0},bad,std::numeric_limits<int>::max(),1);}
            catch(const std::invalid_argument&){forbidden=true;}
            require(forbidden,"Viewport aspect must not create a singular projection from a finite FOV");
        }
        // The raylib matrix stack has a fixed capacity. A failed low-level
        // push followed by an unconditional pop corrupts an outer transform.
        runtime.poll();runtime.begin_frame();
        {
            std::vector<std::unique_ptr<TransformScope>> transforms;
            bool bounded=false;
            for(int i=0;i<64;++i) {
                try {transforms.push_back(std::make_unique<TransformScope>(Vector3{1,0,0}));}
                catch(const std::logic_error&) {bounded=true;break;}
            }
            require(bounded,"Nested transforms must reject matrix stack exhaustion");
            while(!transforms.empty()) transforms.pop_back();
            require(std::abs(rlGetMatrixTransform().m12)<.001f,"Rejected transform must preserve the outer matrix stack");
        }
        {
            TransformScope transform({1,0,0});
            bool forbidden=false;try {CameraPass pass(camera);}catch(const std::logic_error&){forbidden=true;}
            require(forbidden,"A camera cannot replace matrices inside a transform scope");
        }
        runtime.end_frame();
        std::cout<<"Framework 3D render tests passed\n";return 0;
    } catch(const std::exception& error) {std::cerr<<error.what()<<'\n';return 1;}
}
