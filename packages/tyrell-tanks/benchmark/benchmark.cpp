// Original display-pipeline benchmark. No launcher, kernel or boot changes.
#include <algorithm>
#include <array>
#include <chrono>
#include <cctype>
#include <cmath>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#include <cerrno>
#if defined(__unix__) || defined(__APPLE__)
#include <unistd.h>
#endif

#ifdef __linux__
#include <fcntl.h>
#include <linux/fb.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#endif
#ifdef TANKS_BENCH_RLSW
#include "raylib.h"
#include "rlgl.h"
#endif
#ifdef TANKS_BENCH_GLES
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#ifndef EGL_PLATFORM_SURFACELESS_MESA
#define EGL_PLATFORM_SURFACELESS_MESA 0x31DD
#endif
#endif

namespace {
using Clock = std::chrono::steady_clock;
constexpr int kWidth = 320, kHeight = 170;
constexpr float kPi = 3.14159265358979323846f;
volatile std::sig_atomic_t stopped = 0;
void stop(int) { stopped = 1; }
double milliseconds(Clock::time_point a, Clock::time_point b) {
    return std::chrono::duration<double, std::milli>(b-a).count();
}
struct Options {
    double seconds=10, warmup=1, fps=30;
    std::uint64_t frames=0;
    std::string fb="auto", output="-", frame_output;
    bool commit=true, allow_software=false;
};
double number(const std::string& text, double low, double high) {
    std::size_t end=0;
    const double value=std::stod(text, &end);
    if(end!=text.size() || !std::isfinite(value) || value<low || value>high)
        throw std::runtime_error("Invalid numeric argument: "+text);
    return value;
}
Options parse(int argc, char** argv) {
    Options result;
    for(int i=1;i<argc;++i) {
        const std::string key=argv[i];
        auto value=[&]() -> std::string {
            if(++i>=argc) throw std::runtime_error("Missing value for "+key);
            return argv[i];
        };
        if(key=="--seconds") result.seconds=number(value(), 0.01, 600);
        else if(key=="--warmup") result.warmup=number(value(), 0, 30);
        else if(key=="--fps") result.fps=number(value(), 0, 240);
        else if(key=="--frames") {
            const auto input=value();
            const double count=number(input, 1, 1000000);
            if(std::floor(count)!=count) throw std::runtime_error("--frames must be an integer");
            result.frames=static_cast<std::uint64_t>(count);
        }
        else if(key=="--fb") result.fb=value();
        else if(key=="--output") result.output=value();
        else if(key=="--frame-output") result.frame_output=value();
        else if(key=="--no-commit") result.commit=false;
        else if(key=="--allow-software") result.allow_software=true;
        else if(key=="--help") {
            std::cout << "Usage: " << argv[0] << " [--seconds 10] [--warmup 1] [--fps 30|0]\n"
                "  [--frames N] [--fb auto|none|/dev/fb_lcd] [--no-commit]\n"
                "  [--output report.json|-] [--frame-output frame.ppm] [--allow-software]\n"
                "Stops at the first of seconds, frame limit, SIGINT or SIGTERM.\n"
                "--fb none is a headless smoke test, not a physical display measurement.\n";
            std::exit(0);
        } else throw std::runtime_error("Unknown option: "+key);
    }
    return result;
}
std::string quote(const std::string& input) {
    std::ostringstream out;
    out << '"';
    for(unsigned char c:input) {
        if(c=='"' || c=='\\') out << '\\' << c;
        else if(c<32) out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << int(c) << std::dec;
        else out << c;
    }
    out << '"';
    return out.str();
}
struct Vec3 { float x,y,z; };
Vec3 sub(Vec3 a, Vec3 b) { return {a.x-b.x,a.y-b.y,a.z-b.z}; }
float dot(Vec3 a,Vec3 b) { return a.x*b.x+a.y*b.y+a.z*b.z; }
Vec3 cross(Vec3 a,Vec3 b) { return {a.y*b.z-a.z*b.y,a.z*b.x-a.x*b.z,a.x*b.y-a.y*b.x}; }
Vec3 normalized(Vec3 a) { const float n=std::sqrt(dot(a,a)); return {a.x/n,a.y/n,a.z/n}; }
using Mat4=std::array<float,16>;
Mat4 view_matrix() {
    const Vec3 eye{0,13,-23}, target{0,0,1};
    const auto f=normalized(sub(target,eye)), s=normalized(cross(f,{0,1,0})), u=cross(s,f);
    return {s.x,u.x,-f.x,0,s.y,u.y,-f.y,0,s.z,u.z,-f.z,0,-dot(s,eye),-dot(u,eye),dot(f,eye),1};
}
Mat4 projection_matrix() {
    const float f=1/std::tan(60*kPi/360), near=0.1f, far=100;
    return {f/(float(kWidth)/kHeight),0,0,0,0,f,0,0,0,0,(far+near)/(near-far),-1,
        0,0,2*far*near/(near-far),0};
}
Mat4 multiply(const Mat4& a,const Mat4& b) {
    Mat4 result{};
    for(int col=0;col<4;++col) for(int row=0;row<4;++row)
        for(int k=0;k<4;++k) result[col*4+row]+=a[k*4+row]*b[col*4+k];
    return result;
}
struct Vertex { float x,y,z,r,g,b; };
using Mesh=std::vector<Vertex>;
void triangle(Mesh& mesh,Vec3 a,Vec3 b,Vec3 c,Vec3 color) {
    for(const auto p:{a,b,c}) mesh.push_back({p.x,p.y,p.z,color.x,color.y,color.z});
}
void box(Mesh& mesh,Vec3 center,Vec3 size,Vec3 color,float yaw=0) {
    std::array<Vec3,8> points{};
    const float cs=std::cos(yaw),sn=std::sin(yaw);
    for(int i=0;i<8;++i) {
        const float x=((i&1)?0.5f:-0.5f)*size.x;
        const float y=((i&2)?0.5f:-0.5f)*size.y;
        const float z=((i&4)?0.5f:-0.5f)*size.z;
        points[i]={center.x+x*cs+z*sn,center.y+y,center.z-x*sn+z*cs};
    }
    constexpr int faces[6][4]={{0,4,6,2},{1,3,7,5},{0,1,5,4},{2,6,7,3},{0,2,3,1},{4,5,7,6}};
    constexpr float shades[6]={0.65f,0.8f,0.55f,1,0.7f,0.9f};
    for(int f=0;f<6;++f) {
        const auto color2=Vec3{color.x*shades[f],color.y*shades[f],color.z*shades[f]};
        triangle(mesh,points[faces[f][0]],points[faces[f][1]],points[faces[f][2]],color2);
        triangle(mesh,points[faces[f][0]],points[faces[f][2]],points[faces[f][3]],color2);
    }
}
Mesh scene(double time) {
    Mesh result;
    result.reserve(1500);
    triangle(result,{-16,0,-12},{16,0,-12},{16,0,12},{0.21f,0.25f,0.19f});
    triangle(result,{-16,0,-12},{16,0,12},{-16,0,12},{0.21f,0.25f,0.19f});
    for(int x=-14;x<=14;x+=4) for(int z=-10;z<=10;z+=4)
        box(result,{float(x),0.015f,float(z)},{0.04f,0.02f,0.8f},{0.34f,0.38f,0.27f});
    box(result,{0,1,12},{32,2,0.5f},{0.50f,0.42f,0.32f});
    box(result,{-16,1,0},{0.5f,2,24},{0.50f,0.42f,0.32f});
    box(result,{16,1,0},{0.5f,2,24},{0.50f,0.42f,0.32f});
    for(int i=0;i<8;++i) box(result,{float((i%4)*7-10),1.2f,float((i/4)*9-3)},
        {2.4f,2.4f,2.4f},{0.56f,0.47f,0.32f});
    constexpr Vec3 colors[4]={{0.25f,0.75f,0.45f},{0.80f,0.30f,0.25f},{0.28f,0.48f,0.90f},{0.9f,0.72f,0.2f}};
    for(int i=0;i<4;++i) {
        const float a=float(time)*0.35f+i*kPi/2;
        const float x=std::sin(a)*9,z=std::cos(a)*6;
        box(result,{x,0.45f,z},{1.8f,0.7f,2.5f},colors[i],a);
        box(result,{x,1,z},{1.2f,0.6f,1.4f},colors[i],a+0.5f);
        box(result,{x+std::sin(a+0.5f),1,z+std::cos(a+0.5f)},{0.22f,0.22f,2.2f},colors[i],a+0.5f);
    }
    return result;
}

// GLES readback is bottom first. raylib 6.2 rlsw exports top-first rows.
void convert(const std::vector<std::uint8_t>& rgba,std::vector<std::uint16_t>& rgb) {
    for(int y=0;y<kHeight;++y) for(int x=0;x<kWidth;++x) {
#ifdef TANKS_BENCH_GLES
        const std::size_t source=(std::size_t(kHeight-1-y)*kWidth+x)*4;
#else
        const std::size_t source=(std::size_t(y)*kWidth+x)*4;
#endif
        rgb[std::size_t(y)*kWidth+x]=std::uint16_t(((rgba[source]&0xf8)<<8)|
            ((rgba[source+1]&0xfc)<<3)|(rgba[source+2]>>3));
    }
}

class Output {
public:
    std::string path="none", restore_error;
    std::uint64_t commits=0;
    explicit Output(const Options& options):commit_(options.commit) {
        if(options.fb=="none") return;
#ifdef __linux__
        path=options.fb=="auto"?discover():options.fb;
        fd_=::open(path.c_str(),O_RDWR|O_CLOEXEC);
        if(fd_<0) throw std::runtime_error("Cannot open "+path+": "+std::strerror(errno));
        try {
            fb_fix_screeninfo fix{}; fb_var_screeninfo variable{};
            if(ioctl(fd_,FBIOGET_FSCREENINFO,&fix)<0 || ioctl(fd_,FBIOGET_VSCREENINFO,&variable)<0)
                throw std::runtime_error("Cannot query framebuffer geometry");
            if(variable.xres!=kWidth || variable.yres!=kHeight || variable.bits_per_pixel!=16 ||
                variable.red.offset!=11 || variable.red.length!=5 || variable.green.offset!=5 ||
                variable.green.length!=6 || variable.blue.offset!=0 || variable.blue.length!=5 ||
                variable.red.msb_right || variable.green.msb_right || variable.blue.msb_right ||
                variable.transp.length || variable.grayscale || variable.nonstd ||
                fix.type!=FB_TYPE_PACKED_PIXELS || fix.visual!=FB_VISUAL_TRUECOLOR)
                throw std::runtime_error("Framebuffer must be native 320x170 RGB565; refusing another display");
            if(variable.xres_virtual<variable.xres || variable.yres_virtual<variable.yres ||
                variable.xoffset>variable.xres_virtual-variable.xres ||
                variable.yoffset>variable.yres_virtual-variable.yres ||
                std::uint64_t(variable.xoffset+variable.xres)*2>fix.line_length)
                throw std::runtime_error("Framebuffer visible region exceeds virtual geometry or row stride");
            stride_=fix.line_length;
            offset_=std::size_t(variable.yoffset)*stride_+std::size_t(variable.xoffset)*2;
            length_=fix.smem_len;
            if(stride_<kWidth*2 || offset_+std::size_t(kHeight-1)*stride_+kWidth*2>length_)
                throw std::runtime_error("Framebuffer memory geometry is inconsistent");
            map_=static_cast<std::uint8_t*>(mmap(nullptr,length_,PROT_READ|PROT_WRITE,MAP_SHARED,fd_,0));
            if(map_==MAP_FAILED) { map_=nullptr; throw std::runtime_error("Cannot mmap framebuffer"); }
            saved_.resize(kWidth*kHeight*2);
            for(int y=0;y<kHeight;++y) std::memcpy(saved_.data()+y*kWidth*2,map_+offset_+y*stride_,kWidth*2);
        } catch(...) { close(); throw; }
#else
        throw std::runtime_error("Framebuffer output is Linux only; use --fb none for a smoke test");
#endif
    }
    ~Output() { restore(); close(); }
    Output(const Output&)=delete;
    Output& operator=(const Output&)=delete;
    double present(const std::vector<std::uint16_t>& rgb) {
#ifdef __linux__
        if(!map_) return 0;
        for(int y=0;y<kHeight;++y) std::memcpy(map_+offset_+y*stride_,rgb.data()+y*kWidth,kWidth*2);
        if(commit_) {
            const auto start=Clock::now();
            commit();
            return milliseconds(start,Clock::now());
        }
#else
        (void)rgb;
#endif
        return 0;
    }
    void restore() noexcept {
#ifdef __linux__
        if(!map_ || saved_.empty()) return;
        for(int y=0;y<kHeight;++y) std::memcpy(map_+offset_+y*stride_,saved_.data()+y*kWidth*2,kWidth*2);
        saved_.clear();
        if(commit_) try { commit(); } catch(const std::exception& e) { restore_error=e.what(); }
#endif
    }
private:
    bool commit_;
#ifdef __linux__
    int fd_=-1;
    std::uint8_t* map_=nullptr;
    std::size_t stride_=0,offset_=0,length_=0;
    std::vector<std::uint8_t> saved_;
    static std::string discover() {
        struct stat status{};
        if(stat("/dev/fb_lcd",&status)==0 && S_ISCHR(status.st_mode)) return "/dev/fb_lcd";
        std::ifstream entries("/proc/fb");
        std::string line;
        while(std::getline(entries,line)) {
            std::istringstream in(line); int index=-1; std::string name;
            if(in>>index>>name && index>=0 && name.find("st7789")!=std::string::npos)
                return "/dev/fb"+std::to_string(index);
        }
        // DRM fb helpers can have a generic name: inspect the bound SPI parent.
        for(int index=0;index<16;++index) {
            const std::string node="/sys/class/graphics/fb"+std::to_string(index)+"/device/driver";
            std::array<char,1024> link{};
            const auto length=readlink(node.c_str(),link.data(),link.size()-1);
            if(length>0 && std::string(link.data(),std::size_t(length)).find("st7789")!=std::string::npos)
                return "/dev/fb"+std::to_string(index);
        }
        throw std::runtime_error("Cannot identify LCD. Use --fb /dev/fb_lcd or verified native LCD path; --fb none is headless");
    }
    void commit() {
        ++commits;
        while(fsync(fd_)<0) {
            if(errno==EINTR) continue;
            throw std::runtime_error("Framebuffer fsync failed: "+std::string(std::strerror(errno)));
        }
    }
    void close() noexcept {
        if(map_) munmap(map_,length_);
        if(fd_>=0) ::close(fd_);
        map_=nullptr; fd_=-1;
    }
#else
    void close() noexcept {}
#endif
};

#ifdef TANKS_BENCH_RLSW
// rlsw 6.2 emits its initialization message with printf independently of
// SetTraceLogLevel. Keep stdout a valid JSON document when --output is '-'.
class InitializationLogRedirect {
public:
    InitializationLogRedirect() {
        std::fflush(stdout);
        saved_=dup(STDOUT_FILENO);
        if(saved_<0 || dup2(STDERR_FILENO,STDOUT_FILENO)<0) {
            if(saved_>=0) ::close(saved_);
            throw std::runtime_error("Cannot redirect renderer initialization log");
        }
    }
    ~InitializationLogRedirect() {
        std::fflush(stdout);
        dup2(saved_,STDOUT_FILENO); ::close(saved_);
    }
private:
    int saved_=-1;
};
class Renderer {
public:
    std::string name="raylib 6.2 rlsw software rasterizer", vendor="CPU", version="OpenGL 1.1 style";
    bool accelerated=false;
    explicit Renderer(bool) {
        InitializationLogRedirect redirect;
        SetTraceLogLevel(LOG_WARNING);
        InitWindow(kWidth,kHeight,"Tyrell Tanks display benchmark");
        if(!IsWindowReady() || rlGetVersion()!=RL_OPENGL_SOFTWARE)
            throw std::runtime_error("raylib memory software renderer failed to initialize");
    }
    ~Renderer() { CloseWindow(); }
    void render(const Mesh& mesh) {
        BeginDrawing();
        ClearBackground({20,27,38,255});
        const auto projection=projection_matrix(),view=view_matrix();
        rlMatrixMode(RL_PROJECTION); rlLoadIdentity(); rlMultMatrixf(projection.data());
        rlMatrixMode(RL_MODELVIEW); rlLoadIdentity(); rlMultMatrixf(view.data());
        rlEnableDepthTest(); rlDisableBackfaceCulling();
        rlSetTexture(0);
        rlBegin(RL_TRIANGLES);
        for(const auto& v:mesh) {
            rlColor4ub(static_cast<unsigned char>(v.r*255),static_cast<unsigned char>(v.g*255),
                static_cast<unsigned char>(v.b*255),255);
            rlVertex3f(v.x,v.y,v.z);
        }
        rlEnd();
        EndDrawing(); // SUPPORT_CUSTOM_FRAME_CONTROL: flush only, no implicit copy or pacing.
    }
    void readback(std::vector<std::uint8_t>& rgba) {
        rlCopyFramebuffer(0,0,kWidth,kHeight,PIXELFORMAT_UNCOMPRESSED_R8G8B8A8,rgba.data());
    }
};
constexpr const char* backend="rlsw";
#endif

#ifdef TANKS_BENCH_GLES
class Renderer {
public:
    std::string name,vendor,version;
    bool accelerated=false;
    explicit Renderer(bool allow_software) {
        try { initialize(allow_software); }
        catch(...) { cleanup(); throw; }
    }
    ~Renderer() { cleanup(); }
    void render(const Mesh& mesh) {
        glViewport(0,0,kWidth,kHeight);
        glClearColor(20.f/255,27.f/255,38.f/255,1);
        glClear(GL_COLOR_BUFFER_BIT|GL_DEPTH_BUFFER_BIT);
        glUseProgram(program_);
        const auto matrix=multiply(projection_matrix(),view_matrix());
        glUniformMatrix4fv(matrix_,1,GL_FALSE,matrix.data());
        glBindBuffer(GL_ARRAY_BUFFER,buffer_);
        glBufferData(GL_ARRAY_BUFFER,GLsizeiptr(mesh.size()*sizeof(Vertex)),mesh.data(),GL_STREAM_DRAW);
        glVertexAttribPointer(0,3,GL_FLOAT,GL_FALSE,sizeof(Vertex),nullptr);
        glVertexAttribPointer(1,3,GL_FLOAT,GL_FALSE,sizeof(Vertex),reinterpret_cast<void*>(3*sizeof(float)));
        glEnableVertexAttribArray(0); glEnableVertexAttribArray(1);
        glDrawArrays(GL_TRIANGLES,0,GLsizei(mesh.size()));
        glFinish(); // Attribute GPU completion to render, rather than hiding it in readback.
        check("render");
    }
    void readback(std::vector<std::uint8_t>& rgba) {
        glPixelStorei(GL_PACK_ALIGNMENT,1);
        glReadPixels(0,0,kWidth,kHeight,GL_RGBA,GL_UNSIGNED_BYTE,rgba.data());
        check("readback");
    }
private:
    EGLDisplay display_=EGL_NO_DISPLAY;
    EGLSurface surface_=EGL_NO_SURFACE;
    EGLContext context_=EGL_NO_CONTEXT;
    GLuint program_=0,buffer_=0;
    GLint matrix_=-1;
    static void check(const char* stage) {
        const auto error=glGetError();
        if(error!=GL_NO_ERROR) throw std::runtime_error(std::string("GLES error in ")+stage+": "+std::to_string(error));
    }
    static GLuint shader(GLenum type,const char* code) {
        const auto handle=glCreateShader(type);
        glShaderSource(handle,1,&code,nullptr); glCompileShader(handle);
        GLint okay=0; glGetShaderiv(handle,GL_COMPILE_STATUS,&okay);
        if(!okay) {
            std::array<char,2048> log{}; glGetShaderInfoLog(handle,log.size(),nullptr,log.data());
            glDeleteShader(handle); throw std::runtime_error("Shader compilation failed: "+std::string(log.data()));
        }
        return handle;
    }
    void initialize(bool allow_software) {
        const auto get_platform=reinterpret_cast<PFNEGLGETPLATFORMDISPLAYEXTPROC>(eglGetProcAddress("eglGetPlatformDisplayEXT"));
        if(get_platform) display_=get_platform(EGL_PLATFORM_SURFACELESS_MESA,EGL_DEFAULT_DISPLAY,nullptr);
        if(display_==EGL_NO_DISPLAY || !eglInitialize(display_,nullptr,nullptr)) {
            display_=eglGetDisplay(EGL_DEFAULT_DISPLAY);
            if(display_==EGL_NO_DISPLAY || !eglInitialize(display_,nullptr,nullptr))
                throw std::runtime_error("Cannot initialize surfaceless/default EGL; no usable device driver");
        }
        if(!eglBindAPI(EGL_OPENGL_ES_API)) throw std::runtime_error("Cannot bind GLES API");
        const EGLint attributes[]={EGL_SURFACE_TYPE,EGL_PBUFFER_BIT,EGL_RENDERABLE_TYPE,EGL_OPENGL_ES2_BIT,
            EGL_RED_SIZE,8,EGL_GREEN_SIZE,8,EGL_BLUE_SIZE,8,EGL_ALPHA_SIZE,8,EGL_DEPTH_SIZE,16,EGL_NONE};
        EGLConfig config{}; EGLint count=0;
        if(!eglChooseConfig(display_,attributes,&config,1,&count) || count<1)
            throw std::runtime_error("Cannot choose GLES2 RGBA8/depth16 pbuffer config");
        const EGLint size[]={EGL_WIDTH,kWidth,EGL_HEIGHT,kHeight,EGL_NONE};
        surface_=eglCreatePbufferSurface(display_,config,size);
        const EGLint context_attributes[]={EGL_CONTEXT_CLIENT_VERSION,2,EGL_NONE};
        context_=eglCreateContext(display_,config,EGL_NO_CONTEXT,context_attributes);
        if(surface_==EGL_NO_SURFACE || context_==EGL_NO_CONTEXT || !eglMakeCurrent(display_,surface_,surface_,context_))
            throw std::runtime_error("Cannot create/bind GLES2 pbuffer context");
        auto gl_string=[](GLenum key) { const auto p=glGetString(key); return p?std::string(reinterpret_cast<const char*>(p)):std::string("unknown"); };
        name=gl_string(GL_RENDERER); vendor=gl_string(GL_VENDOR); version=gl_string(GL_VERSION);
        std::string lower=name; std::transform(lower.begin(),lower.end(),lower.begin(),[](unsigned char c){return char(std::tolower(c));});
        const bool cpu=lower.find("llvmpipe")!=std::string::npos || lower.find("softpipe")!=std::string::npos ||
            lower.find("swrast")!=std::string::npos || lower.find("software")!=std::string::npos || lower.find("swiftshader")!=std::string::npos;
        // Positive identification avoids labeling an unknown renderer hardware accelerated.
        accelerated=!cpu && (lower.find("vc4")!=std::string::npos || lower.find("v3d")!=std::string::npos ||
            lower.find("videocore")!=std::string::npos || lower.find("virgl")!=std::string::npos ||
            lower.find("intel")!=std::string::npos || lower.find("radeon")!=std::string::npos ||
            lower.find("nvidia")!=std::string::npos || lower.find("mali")!=std::string::npos || lower.find("adreno")!=std::string::npos);
        if(!accelerated && !allow_software)
            throw std::runtime_error("GLES renderer is software or unrecognized: "+name+"; --allow-software is for functional tests only");
        const GLuint vertex=shader(GL_VERTEX_SHADER,
            "attribute vec3 position; attribute vec3 color; uniform mat4 viewProjection; varying vec3 vColor;"
            "void main(){ gl_Position=viewProjection*vec4(position,1.0); vColor=color; }");
        GLuint fragment=0;
        try { fragment=shader(GL_FRAGMENT_SHADER,
            "precision mediump float; varying vec3 vColor; void main(){ gl_FragColor=vec4(vColor,1.0); }"); }
        catch(...) { glDeleteShader(vertex); throw; }
        program_=glCreateProgram(); glAttachShader(program_,vertex); glAttachShader(program_,fragment);
        glBindAttribLocation(program_,0,"position"); glBindAttribLocation(program_,1,"color"); glLinkProgram(program_);
        glDeleteShader(vertex); glDeleteShader(fragment);
        GLint linked=0; glGetProgramiv(program_,GL_LINK_STATUS,&linked);
        if(!linked) throw std::runtime_error("Cannot link GLES benchmark program");
        matrix_=glGetUniformLocation(program_,"viewProjection");
        glGenBuffers(1,&buffer_); glEnable(GL_DEPTH_TEST); glDepthFunc(GL_LEQUAL);
        glDisable(GL_CULL_FACE); glDisable(GL_BLEND); glDisable(GL_DITHER);
        check("initialization");
    }
    void cleanup() noexcept {
        if(display_!=EGL_NO_DISPLAY) {
            if(context_!=EGL_NO_CONTEXT && eglMakeCurrent(display_,surface_,surface_,context_)) {
                if(buffer_) glDeleteBuffers(1,&buffer_);
                if(program_) glDeleteProgram(program_);
            }
            eglMakeCurrent(display_,EGL_NO_SURFACE,EGL_NO_SURFACE,EGL_NO_CONTEXT);
            if(context_!=EGL_NO_CONTEXT) eglDestroyContext(display_,context_);
            if(surface_!=EGL_NO_SURFACE) eglDestroySurface(display_,surface_);
            eglTerminate(display_);
        }
        display_=EGL_NO_DISPLAY;
    }
};
constexpr const char* backend="gles2";
#endif

struct Sample { double scene_ms,render_ms,readback_ms,convert_ms,present_ms,commit_ms,work_ms,interval_ms; };
void metric(std::ostream& out,const std::vector<Sample>& samples,double Sample::*member) {
    std::vector<double> values; values.reserve(samples.size());
    double sum=0;
    for(const auto& sample:samples) { values.push_back(sample.*member); sum+=sample.*member; }
    std::sort(values.begin(),values.end());
    auto percentile=[&](double p) {
        const double index=p*(values.size()-1);
        const auto low=std::size_t(index),high=std::min(low+1,values.size()-1);
        return values[low]+(values[high]-values[low])*(index-low);
    };
    out << "{\"mean_ms\":" << sum/values.size() << ",\"p50_ms\":" << percentile(0.5)
        << ",\"p95_ms\":" << percentile(0.95) << ",\"max_ms\":" << values.back() << '}';
}
void ppm(const std::string& path,const std::vector<std::uint16_t>& pixels) {
    if(path.empty()) return;
    std::ofstream out(path,std::ios::binary);
    if(!out) throw std::runtime_error("Cannot open frame output "+path);
    out << "P6\n" << kWidth << ' ' << kHeight << "\n255\n";
    for(const auto p:pixels) {
        const char rgb[]={char(((p>>11)&31)*255/31),char(((p>>5)&63)*255/63),char((p&31)*255/31)};
        out.write(rgb,3);
    }
    if(!out) throw std::runtime_error("Cannot write frame output "+path);
}
int run(const Options& options) {
    std::signal(SIGINT,stop); std::signal(SIGTERM,stop);
    Renderer renderer(options.allow_software);
    Output output(options);
    std::vector<std::uint8_t> rgba(kWidth*kHeight*4);
    std::vector<std::uint16_t> rgb(kWidth*kHeight);
    std::vector<Sample> samples;
    samples.reserve(std::size_t(std::min(1000000.0,options.seconds*std::max(options.fps,120.0))));
    const auto period=std::chrono::duration<double>(options.fps>0?1/options.fps:0);
    auto phase_start=Clock::now(),next_frame=phase_start,previous=phase_start;
    bool warming=options.warmup>0;
    std::uint64_t index=0,measured_commits=0,missed_deadlines=0;
    double elapsed=0;
    if(!warming) std::cerr << "TANKS_MEASUREMENT_STARTED\n";
    while(!stopped) {
        const auto start=Clock::now();
        const double phase_seconds=std::chrono::duration<double>(start-phase_start).count();
        if(warming && phase_seconds>=options.warmup) {
            warming=false; phase_start=start; previous=start; next_frame=start; index=0;
            measured_commits=output.commits;
            std::cerr << "TANKS_MEASUREMENT_STARTED\n";
        }
        if(!warming && (std::chrono::duration<double>(start-phase_start).count()>=options.seconds ||
            (options.frames && samples.size()>=options.frames))) break;
        const auto mesh=scene(double(index)/(options.fps>0?options.fps:60));
        const auto scene_done=Clock::now();
        renderer.render(mesh);
        const auto render_done=Clock::now();
        renderer.readback(rgba);
        const auto readback_done=Clock::now();
        convert(rgba,rgb);
        const auto convert_done=Clock::now();
        const double commit_ms=output.present(rgb);
        const auto done=Clock::now();
        if(!warming) samples.push_back({milliseconds(start,scene_done),milliseconds(scene_done,render_done),
            milliseconds(render_done,readback_done),milliseconds(readback_done,convert_done),
            milliseconds(convert_done,done),commit_ms,milliseconds(start,done),
            samples.empty()?0:milliseconds(previous,start)});
        previous=start; ++index;
        if(options.fps>0) {
            next_frame+=std::chrono::duration_cast<Clock::duration>(period);
            if(done>next_frame) {
                if(!warming) ++missed_deadlines;
                // Drop pacing debt; do not emit a burst of stale frames.
                next_frame=done;
            } else std::this_thread::sleep_until(next_frame);
        }
    }
    elapsed=std::chrono::duration<double>(Clock::now()-phase_start).count();
    const auto commits=output.commits-measured_commits;
    if(samples.empty()) throw std::runtime_error("No measured frames completed (interrupted during warmup)");
    // File output/restore are deliberately outside the measurement interval.
    ppm(options.frame_output,rgb);
    output.restore();
    std::uint64_t checksum=14695981039346656037ULL;
    for(auto value:rgb) {
        checksum^=value&255; checksum*=1099511628211ULL;
        checksum^=value>>8; checksum*=1099511628211ULL;
    }
    std::ostringstream report;
    report << std::fixed << std::setprecision(6)
        << "{\n  \"schema_version\":1,\n  \"backend\":" << quote(backend)
        << ",\n  \"renderer\":" << quote(renderer.name) << ",\n  \"vendor\":" << quote(renderer.vendor)
        << ",\n  \"api_version\":" << quote(renderer.version)
        << ",\n  \"hardware_accelerated\":" << (renderer.accelerated?"true":"false")
        << ",\n  \"width\":320,\n  \"height\":170,\n  \"scene\":\"shared-untextured-tank-arena-v1\",\n  \"triangles\":" << scene(0).size()/3
        << ",\n  \"fb\":" << quote(output.path) << ",\n  \"physical_display_test\":" << (output.path=="none"?"false":"true")
        << ",\n  \"fsync_enabled\":" << (options.commit && output.path!="none"?"true":"false")
        << ",\n  \"warmup_seconds\":" << options.warmup << ",\n  \"target_fps\":" << options.fps
        << ",\n  \"elapsed_seconds\":" << elapsed << ",\n  \"frames\":" << samples.size()
        << ",\n  \"completed_frame_rate\":" << samples.size()/elapsed << ",\n  \"fsync_calls\":" << commits
        << ",\n  \"missed_pacing_deadlines\":" << missed_deadlines
        << ",\n  \"frame_checksum_fnv1a64\":" << quote(std::to_string(checksum))
        << ",\n  \"interrupted\":" << (stopped?"true":"false")
        << ",\n  \"restore_error\":" << quote(output.restore_error) << ",\n  \"metrics\":{\n";
    const std::array<std::pair<const char*,double Sample::*>,7> fields={{{"scene_build",&Sample::scene_ms},
        {"render",&Sample::render_ms},{"readback",&Sample::readback_ms},{"rgb565_conversion",&Sample::convert_ms},
        {"present_including_commit",&Sample::present_ms},{"fsync",&Sample::commit_ms},{"work_total",&Sample::work_ms}}};
    for(std::size_t i=0;i<fields.size();++i) {
        report << "    " << quote(fields[i].first) << ':'; metric(report,samples,fields[i].second); report << ",\n";
    }
    report << "    \"frame_start_interval\":";
    if(samples.size()>1) {
        auto intervals=samples; intervals.erase(intervals.begin()); metric(report,intervals,&Sample::interval_ms);
    } else report << "null";
    report << "\n  },\n  \"measurement_scope\":\"CPU wall time and completed application frames; fsync is a kernel commit request, not panel-vsync or a verified physical refresh count\"\n}\n";
    if(options.output=="-") std::cout << report.str();
    else {
        std::ofstream file(options.output); if(!file) throw std::runtime_error("Cannot open JSON output "+options.output);
        file << report.str(); if(!file) throw std::runtime_error("Cannot write JSON output "+options.output);
        std::cerr << samples.size() << " frames, " << samples.size()/elapsed << " completed fps; report: " << options.output << '\n';
    }
    return output.restore_error.empty()?0:2;
}
} // namespace
int main(int argc,char** argv) {
    try { return run(parse(argc,argv)); }
    catch(const std::exception& error) { std::cerr << "Display benchmark: " << error.what() << '\n'; return 1; }
}
