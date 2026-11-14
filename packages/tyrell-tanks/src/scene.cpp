#include "scene.hpp"
#include <tyrell/game/render.hpp>
#include <algorithm>
#include <cmath>

namespace tanks {
namespace {
constexpr Color sand{198, 158, 104, 255};
constexpr Color stone{175, 126, 78, 255};
constexpr Color ink{37, 43, 40, 255};
Color shade(Color c,float f) { return tyrell::game::shade_color(c,f); }
void box(float x,float y,float z,float w,float h,float d,Color color) {
    tyrell::game::draw_box({x,y,z},{w,h,d},color);
}
void tank_model(const Tank& tank, int id) {
    tyrell::game::TransformScope model({tank.position.x,0,tank.position.z},0,{.75f,.75f,.75f});
    float x=0,z=0;
    Color color=player_color(id);
    box(x+.18f,.015f,z+.2f,1.9f,.02f,2.2f,{101,88,61,100});
    {
    tyrell::game::TransformScope hull({x,0,z},tank.yaw);
    box(-.72f,.33f,0,.34f,.42f,1.95f,ink);
    box(.72f,.33f,0,.34f,.42f,1.95f,ink);
    for(int j=-2;j<=2;++j) {
        box(-.9f,.33f,j*.35f,.025f,.21f,.09f,{89,91,77,255});
        box(.9f,.33f,j*.35f,.025f,.21f,.09f,{89,91,77,255});
    }
    box(0,.58f,0,1.4f,.48f,1.75f,color);
    box(0,.86f,-.48f,.9f,.09f,.55f,shade(color,.7f));
    box(0,.84f,.57f,1.05f,.08f,.22f,{235,215,162,255});
    }
    {
    tyrell::game::TransformScope turret({x,0,z},tank.turret_yaw);
    box(0,1.0f,0,.91f,.38f,.96f,color);
    box(-.15f,1.22f,-.15f,.36f,.08f,.35f,shade(color,.67f));
    DrawCylinderEx({0,1.05f,.35f},{0,1.05f,1.2f},.11f,.09f,6,ink);
    box(0,1.05f,1.2f,.24f,.2f,.25f,{69,75,64,255});
    if(tank.cooldown>.53f) {
        DrawSphereEx({0,1.05f,1.4f},.18f,3,5,{255,211,99,255});
    }
    }
}
}
Color player_color(int index) {
    constexpr Color colors[]={{80,117,86,255},{197,87,53,255},{71,117,153,255},{170,126,53,255}};
    return colors[std::clamp(index,0,3)];
}
void draw_scene(const World& world,int local,double time,bool overview) {
    Vector3 focus{0,0,0};
    if(!overview && local>=0 && local<kMaxPlayers) {
        focus.x=world.tanks[local].position.x*.65f;
        focus.z=world.tanks[local].position.z*.65f;
    }
    Camera3D camera{};
    camera.position=overview?Vector3{26,32,30}:Vector3{focus.x+14,23,focus.z+21};
    camera.target=focus; camera.up={0,1,0}; camera.fovy=overview?33.0f:25.0f;
    camera.projection=CAMERA_ORTHOGRAPHIC;
    ClearBackground({225,192,140,255});
    {
    tyrell::game::CameraPass pass(camera);
    DrawPlane({0,-.045f,0},{95,95},sand);
    box(0,-.005f,0,kArenaWidth,.04f,kArenaDepth,{211,175,119,255});
    box(0,.022f,0,4,.015f,kArenaDepth,{195,157,105,255});
    box(0,.023f,0,kArenaWidth,.015f,3,{195,157,105,255});
    for(int i=-14;i<=14;i+=4) box(i,.035f,0,1.5f,.02f,.09f,{221,190,139,255});
    for(int i=-10;i<=10;i+=4) box(0,.035f,i,.09f,.02f,1.5f,{221,190,139,255});
    for(const auto& o:obstacles()) {
        box(o.position.x+.25f,.03f,o.position.z+.3f,o.width+.3f,.02f,o.depth+.5f,{150,121,82,255});
        box(o.position.x,o.height/2,o.position.z,o.width,o.height,o.depth,stone);
        box(o.position.x,o.height+.035f,o.position.z,o.width+.12f,.09f,o.depth+.12f,{202,156,100,255});
    }
    for(int i=-16;i<16;i+=2) {
        box(i+1,.25f,-12.4f,1.9f,.5f,.65f,stone);
        box(i+1,.25f,12.4f,1.9f,.5f,.65f,stone);
    }
    for(int i=-12;i<12;i+=2) {
        box(-16.4f,.25f,i+1,.65f,.5f,1.9f,stone);
        box(16.4f,.25f,i+1,.65f,.5f,1.9f,stone);
    }
    // Sparse faceted scenery stays outside the collision arena.
    for(int i=0;i<14;++i) {
        float x=std::sin(i*19.17f)*24, z=(i%2?1:-1)*(16+i%4);
        DrawCylinder({x,.5f,z},.8f,0,1.0f,5,shade(stone,.85f));
    }
    for(int i=0;i<kMaxPlayers;++i) {
        const auto& tank=world.tanks[i];
        if(!tank.active) continue;
        if(tank.health>0) tank_model(tank,i);
        else {
            DrawCylinder({tank.position.x,.015f,tank.position.z},1.1f,1.1f,.02f,8,{114,101,79,255});
            box(tank.position.x,.25f,tank.position.z,1.2f,.45f,1.5f,{68,68,55,255});
        }
    }
    for(const auto& shell:world.shells) if(shell.active) {
        DrawSphereEx({shell.position.x,.8f,shell.position.z},.13f,3,4,{255,232,157,255});
        float scale=.035f;
        DrawLine3D({shell.position.x,.8f,shell.position.z},
            {shell.position.x-shell.velocity.x*scale,.8f,shell.position.z-shell.velocity.z*scale},{245,179,74,255});
    }
    if(local>=0 && local<kMaxPlayers && world.tanks[local].health>0 && !overview) {
        const auto& t=world.tanks[local];
        Vector3 mark{t.position.x+std::sin(t.turret_yaw)*4,.08f,t.position.z+std::cos(t.turret_yaw)*4};
        DrawCircle3D(mark,.32f,{1,0,0},90,{242,229,181,255});
        DrawLine3D({mark.x-.5f,.08f,mark.z},{mark.x+.5f,.08f,mark.z},{242,229,181,255});
        DrawLine3D({mark.x,.08f,mark.z-.5f},{mark.x,.08f,mark.z+.5f},{242,229,181,255});
    }
    }
    if(!overview) for(int i=0;i<kMaxPlayers;++i) {
        const auto& t=world.tanks[i];
        if(!t.active || t.health<=0) continue;
        Vector2 point=tyrell::game::world_to_screen({t.position.x,1.9f,t.position.z},camera,320,170);
        DrawRectangle(static_cast<int>(point.x)-8,static_cast<int>(point.y),16,2,{45,48,39,255});
        DrawRectangle(static_cast<int>(point.x)-8,static_cast<int>(point.y),t.health*16/100,2,player_color(i));
    }
    (void)time;
}
}
