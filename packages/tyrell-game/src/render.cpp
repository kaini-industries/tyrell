#include <tyrell/game/render.hpp>
#include "rlgl.h"
#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>

namespace tyrell::game {
namespace {
bool camera_active=false;
int transform_depth=0;
// Reserve one slot for a camera and one for ordinary raylib draw helpers.
constexpr int maximum_transform_depth=RL_MAX_MATRIX_STACK_SIZE-2;
bool finite(Vector3 v) { return std::isfinite(v.x)&&std::isfinite(v.y)&&std::isfinite(v.z); }
bool valid_camera(const Camera3D& c) {
    if(!finite(c.position)||!finite(c.target)||!finite(c.up)||!std::isfinite(c.fovy)||c.fovy<=0||
       (c.projection!=CAMERA_PERSPECTIVE && c.projection!=CAMERA_ORTHOGRAPHIC)||
       (c.projection==CAMERA_PERSPECTIVE && c.fovy>=180)) return false;
    const double x=double(c.target.x)-c.position.x,y=double(c.target.y)-c.position.y,z=double(c.target.z)-c.position.z;
    const double direction=x*x+y*y+z*z,up=double(c.up.x)*c.up.x+double(c.up.y)*c.up.y+double(c.up.z)*c.up.z;
    const double a=y*c.up.z-z*c.up.y,b=z*c.up.x-x*c.up.z,d=x*c.up.y-y*c.up.x;
    // Reject singular/near-parallel axes and values that would overflow the
    // float math used by raylib's view-matrix normalization.
    if(!(direction>0 && up>0 && direction<=std::numeric_limits<float>::max() &&
         up<=std::numeric_limits<float>::max() && a*a+b*b+d*d>direction*up*1e-12)) return false;
    // Finite inputs can still underflow float normalization or overflow the
    // view translation. Check the exact float matrix raylib will consume.
    const Matrix view=GetCameraMatrix(c);
    if(!std::isfinite(view.m12)||!std::isfinite(view.m13)||!std::isfinite(view.m14)) return false;
    auto unit=[](float x,float y,float z) {
        const double norm=double(x)*x+double(y)*y+double(z)*z;
        return norm>.999 && norm<1.001;
    };
    return unit(view.m0,view.m4,view.m8) && unit(view.m1,view.m5,view.m9) &&
        unit(view.m2,view.m6,view.m10);
}
bool valid_projection(const Camera3D& c,double aspect) {
    const double near=rlGetCullDistanceNear(),far=rlGetCullDistanceFar();
    if(!std::isfinite(aspect)||aspect<=0||!std::isfinite(near)||!std::isfinite(far)||far<=near||
       (c.projection==CAMERA_PERSPECTIVE && near<=0)) return false;
    const double top=c.projection==CAMERA_PERSPECTIVE?
        near*std::tan(double(c.fovy)*.5*DEG2RAD):double(c.fovy)/2;
    // rlFrustum/rlOrtho convert extents to float before dividing. Validate that
    // exact arithmetic, including viewport aspect and custom clipping planes,
    // before BeginMode3D can mutate the graphics state.
    auto as_float=[](double value,float& result) {
        if(!std::isfinite(value)||std::abs(value)>std::numeric_limits<float>::max()) return false;
        result=static_cast<float>(value);return true;
    };
    float horizontal=0,vertical=0,depth=0,near_f=0,far_f=0;
    if(!as_float(2*top*aspect,horizontal)||!as_float(2*top,vertical)||!as_float(far-near,depth)||
       !as_float(near,near_f)||!as_float(far,far_f)||horizontal<=0||vertical<=0||depth<=0) return false;
    const bool perspective=c.projection==CAMERA_PERSPECTIVE;
    const float numerator=perspective?near_f*2:2;
    const float x=numerator/horizontal,y=numerator/vertical;
    const float z=perspective?-(far_f+near_f)/depth:-2/depth;
    const float offset=perspective?-(far_f*near_f*2)/depth:-(far_f+near_f)/depth;
    return std::isfinite(x)&&x>0&&std::isfinite(y)&&y>0&&std::isfinite(z)&&z!=0&&
        std::isfinite(offset)&&(!perspective||offset!=0);
}
}
Color shade_color(Color c,float f) {
    f=std::isfinite(f)?std::clamp(f,0.0f,1.0f):0.0f;
    return {static_cast<unsigned char>(c.r*f),static_cast<unsigned char>(c.g*f),
            static_cast<unsigned char>(c.b*f),c.a};
}
bool draw_box(Vector3 p,Vector3 s,Color color,const BoxLighting& lighting) {
    if(!IsWindowReady() || !finite(p) || !finite(s) || s.x<=0 || s.y<=0 || s.z<=0) return false;
    const float l=p.x-s.x/2,r=p.x+s.x/2,b=p.y-s.y/2,t=p.y+s.y/2,n=p.z-s.z/2,f=p.z+s.z/2;
    if(!finite({l,b,n}) || !finite({r,t,f})) return false;
    rlBegin(RL_QUADS);
    auto face=[&](int index,Vector3 a,Vector3 c,Vector3 e,Vector3 g) {
        const Color v=shade_color(color,lighting.faces[index]);rlColor4ub(v.r,v.g,v.b,v.a);
        for(Vector3 point:{a,c,e,g}) rlVertex3f(point.x,point.y,point.z);
    };
    face(0,{l,t,n},{l,t,f},{r,t,f},{r,t,n});
    face(1,{l,b,f},{l,b,n},{r,b,n},{r,b,f});
    face(2,{l,b,f},{r,b,f},{r,t,f},{l,t,f});
    face(3,{r,b,n},{l,b,n},{l,t,n},{r,t,n});
    face(4,{l,b,n},{l,b,f},{l,t,f},{l,t,n});
    face(5,{r,b,f},{r,b,n},{r,t,n},{r,t,f});
    rlEnd();return true;
}
TransformScope::TransformScope(Vector3 p,float yaw,Vector3 scale) {
    if(!IsWindowReady()) throw std::logic_error("TransformScope requires an active Runtime");
    if(!finite(p)||!finite(scale)||!std::isfinite(yaw)) throw std::invalid_argument("Invalid transform");
    if(transform_depth>=maximum_transform_depth)
        throw std::logic_error("TransformScope matrix stack capacity exceeded");
    const float wrapped=static_cast<float>(std::remainder(double(yaw),2.0*3.14159265358979323846));
    rlPushMatrix();rlTranslatef(p.x,p.y,p.z);rlRotatef(wrapped*RAD2DEG,0,1,0);rlScalef(scale.x,scale.y,scale.z);
    ++transform_depth;
}
TransformScope::~TransformScope() { rlPopMatrix();--transform_depth; }
CameraPass::CameraPass(const Camera3D& camera) {
    if(!IsWindowReady()) throw std::logic_error("CameraPass requires an active Runtime");
    if(camera_active) throw std::logic_error("CameraPass scopes cannot be nested");
    if(transform_depth) throw std::logic_error("Begin CameraPass outside all TransformScope scopes");
    const int width=GetRenderWidth(),height=GetRenderHeight();
    if(!valid_camera(camera)||width<=0||height<=0||!valid_projection(camera,float(width)/float(height)))
        throw std::invalid_argument("Invalid 3D camera or projection");
    BeginMode3D(camera);camera_active=true;
}
CameraPass::~CameraPass() { EndMode3D();camera_active=false; }
Vector2 world_to_screen(Vector3 position,const Camera3D& camera,int width,int height) {
    if(width<=0 || height<=0 || !finite(position)||!valid_camera(camera)||
       !valid_projection(camera,double(width)/height))
        throw std::invalid_argument("Invalid camera projection or viewport");
    const auto projected=GetWorldToScreenEx(position,camera,width,height);
    if(!std::isfinite(projected.x)||!std::isfinite(projected.y))
        throw std::invalid_argument("Point has no finite camera projection");
    return projected;
}
} // namespace tyrell::game
