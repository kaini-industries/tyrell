#pragma once
#include "raylib.h"
#include <array>

namespace tyrell::game {
// Fixed per-face lighting works with both GLES/OpenGL and raylib's software
// rasterizer. Face order: top, bottom, front(+Z), back, left, right.
struct BoxLighting {
    std::array<float,6> faces{{1.0f,.55f,.78f,.64f,.66f,.88f}};
};
Color shade_color(Color color,float brightness);
// Returns false for invalid geometry or when no graphics context exists.
bool draw_box(Vector3 center,Vector3 size,Color color,const BoxLighting& lighting={});

class TransformScope {
public:
    // Nest inside a camera pass; nesting is bounded by raylib's matrix stack
    // (30 transforms with the pinned backend). Excess nesting throws.
    explicit TransformScope(Vector3 position={0,0,0},float yaw_radians=0,Vector3 scale={1,1,1});
    ~TransformScope();
    TransformScope(const TransformScope&)=delete;
    TransformScope& operator=(const TransformScope&)=delete;
};
class CameraPass {
public:
    // Non-nestable; begin outside transforms and close before HUD/end_frame().
    explicit CameraPass(const Camera3D& camera);
    ~CameraPass();
    CameraPass(const CameraPass&)=delete;
    CameraPass& operator=(const CameraPass&)=delete;
};
// Always supply the logical viewport. Invalid/singular projections throw;
// finite projection alone is not a visibility test for points behind a camera.
Vector2 world_to_screen(Vector3 position,const Camera3D& camera,int width,int height);
} // namespace tyrell::game
