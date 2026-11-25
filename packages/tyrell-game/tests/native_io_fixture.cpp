// Link-time syscall wrappers drive the production native bridge and Runtime.
// No framebuffer, evdev or other device node is opened by these fixtures.
#include "native_io_fixture.hpp"
#include "raylib.h"

#include <algorithm>
#include <array>
#include <cerrno>
#include <cstdarg>
#include <cstdint>
#include <csignal>
#include <cstring>
#include <deque>
#include <fcntl.h>
#include <iostream>
#include <linux/fb.h>
#include <linux/input.h>
#include <set>
#include <stdexcept>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#include <utility>
#include <vector>

namespace {
constexpr int framebuffer_fd=1000;
struct Fixtures {
    std::vector<std::uint16_t> pixels=std::vector<std::uint16_t>(320*170,0x1111);
    std::set<int> keyboards;
    std::vector<unsigned short> held;
    std::deque<input_event> events,after_reopen;
    unsigned commits=0,reads=0,key_opens=0,key_closes=0,fb_closes=0,unmaps=0;
    bool fail_open=false,fail_resync=false,fail_commit=false;
    std::uint16_t open_failure_color=0;
    int read_signal=0;
    std::function<void()> read_callback;
    std::function<void()> close_callback;
    void reset() {*this=Fixtures{};}
    void owner_pixels(std::uint16_t color) {std::fill(pixels.begin(),pixels.end(),color);}
    bool uniform(std::uint16_t color) const {return std::all_of(pixels.begin(),pixels.end(),[&](auto pixel){return pixel==color;});}
    void key(unsigned short code,int value) {
        if(value==0) held.erase(std::remove(held.begin(),held.end(),code),held.end());
        else if(value==1&&std::find(held.begin(),held.end(),code)==held.end()) held.push_back(code);
        input_event event{};event.type=EV_KEY;event.code=code;event.value=value;events.push_back(event);
    }
    void sync(unsigned short code) {input_event event{};event.type=EV_SYN;event.code=code;events.push_back(event);}
} fixture;
}
extern "C" {
int __real_open(const char*,int,...);
int __wrap_open(const char* path,int flags,...) {
    if(std::strcmp(path,"/fixture/lcd")==0) return framebuffer_fd;
    if(std::strcmp(path,"/fixture/keyboard")==0) {
        if(fixture.fail_open) {
            if(fixture.open_failure_color) fixture.owner_pixels(fixture.open_failure_color);
            errno=ENOENT;return -1;
        }
        const int fd=1100+static_cast<int>(++fixture.key_opens);
        fixture.keyboards.insert(fd);
        if(fixture.key_opens>1) fixture.events=fixture.after_reopen;
        return fd;
    }
    if(flags&O_CREAT) {va_list args;va_start(args,flags);const auto mode=va_arg(args,int);va_end(args);return __real_open(path,flags,mode);}
    return __real_open(path,flags);
}
int __real_close(int);
int __wrap_close(int fd) {
    if(fd==framebuffer_fd) {++fixture.fb_closes;return 0;}
    if(fixture.keyboards.erase(fd)) {
        auto callback=std::move(fixture.close_callback);fixture.close_callback={};
        if(callback)callback();
        ++fixture.key_closes;fixture.events.clear();return 0;
    }
    return __real_close(fd);
}
int __real_fstat(int,struct stat*);
int __wrap_fstat(int fd,struct stat* status) {
    if(fixture.keyboards.count(fd)) {std::memset(status,0,sizeof(*status));status->st_mode=S_IFCHR|0600;return 0;}
    return __real_fstat(fd,status);
}
int __real_ioctl(int,unsigned long,...);
int __wrap_ioctl(int fd,unsigned long request,...) {
    va_list args;va_start(args,request);auto* value=va_arg(args,void*);va_end(args);
    if(fd==framebuffer_fd) {
        if(request==FBIOGET_FSCREENINFO) {
            auto& fix=*static_cast<fb_fix_screeninfo*>(value);fix={};
            fix.line_length=640;fix.smem_len=320*170*2;fix.type=FB_TYPE_PACKED_PIXELS;fix.visual=FB_VISUAL_TRUECOLOR;return 0;
        }
        if(request==FBIOGET_VSCREENINFO) {
            auto& var=*static_cast<fb_var_screeninfo*>(value);var={};
            var.xres=var.xres_virtual=320;var.yres=var.yres_virtual=170;var.bits_per_pixel=16;
            var.red.offset=11;var.red.length=5;var.green.offset=5;var.green.length=6;var.blue.length=5;return 0;
        }
    }
    if(fixture.keyboards.count(fd)&&_IOC_TYPE(request)=='E') {
        const auto size=_IOC_SIZE(request);std::memset(value,0,size);
        if(_IOC_NR(request)==_IOC_NR(EVIOCGNAME(1))) {
            const char name[]="tca8418-fixture";std::memcpy(value,name,std::min<std::size_t>(size,sizeof(name)));return 0;
        }
        if(_IOC_NR(request)==_IOC_NR(EVIOCGKEY(1))) {
            if(fixture.fail_resync) {errno=EIO;return -1;}
            auto* bits=static_cast<unsigned char*>(value);
            for(const auto key:fixture.held) if(key/8<size) bits[key/8]|=1U<<(key%8);
            return 0;
        }
        if(_IOC_NR(request)==_IOC_NR(EVIOCGLED(1))) return 0;
    }
    return __real_ioctl(fd,request,value);
}
void* __real_mmap(void*,std::size_t,int,int,int,off_t);
void* __wrap_mmap(void* address,std::size_t length,int protection,int flags,int fd,off_t offset) {
    if(fd==framebuffer_fd) return fixture.pixels.data();
    return __real_mmap(address,length,protection,flags,fd,offset);
}
int __real_munmap(void*,std::size_t);
int __wrap_munmap(void* address,std::size_t length) {
    if(address==fixture.pixels.data()) {++fixture.unmaps;return 0;}
    return __real_munmap(address,length);
}
int __real_fsync(int);
int __wrap_fsync(int fd) {
    if(fd==framebuffer_fd) {
        if(fixture.fail_commit) {errno=EIO;return -1;}
        ++fixture.commits;return 0;
    }
    return __real_fsync(fd);
}
ssize_t __real_read(int,void*,std::size_t);
ssize_t __wrap_read(int fd,void* data,std::size_t count) {
    if(!fixture.keyboards.count(fd)) return __real_read(fd,data,count);
    ++fixture.reads;
    const int signal=fixture.read_signal;
    fixture.read_signal=0;
    if(signal) std::raise(signal);
    auto callback=std::move(fixture.read_callback);
    fixture.read_callback={};
    if(callback)callback();
    if(fixture.events.empty()) {errno=EAGAIN;return -1;}
    const auto number=std::min(count/sizeof(input_event),fixture.events.size());
    auto* events=static_cast<input_event*>(data);
    for(std::size_t i=0;i<number;++i) {events[i]=fixture.events.front();fixture.events.pop_front();}
    return static_cast<ssize_t>(number*sizeof(input_event));
}
}


namespace tyrell::game::test {
void reset_native_fixture() {fixture.reset();}
RuntimeOptions native_fixture_options() {
    RuntimeOptions result;result.backend=Backend::Native;result.fps=0;
    result.framebuffer="/fixture/lcd";result.input="/fixture/keyboard";return result;
}
void write_foreground_pixels(std::uint16_t color) {fixture.owner_pixels(color);}
bool foreground_pixels_equal(std::uint16_t color) {return fixture.uniform(color);}
NativeIoStats native_io_stats() {
    return {fixture.commits,fixture.reads,fixture.key_opens,fixture.key_closes,fixture.fb_closes,fixture.unmaps,
            static_cast<unsigned>(fixture.keyboards.size())};
}
void queue_native_key(unsigned short code,int value) {fixture.key(code,value);}
void queue_native_sync(unsigned short code) {fixture.sync(code);}
void set_native_held(const std::vector<unsigned short>& held) {fixture.held=held;}
void queue_native_resume_key(unsigned short code,int value) {
    input_event event{};event.type=EV_KEY;event.code=code;event.value=value;fixture.after_reopen.push_back(event);
}
void fail_native_keyboard_open(bool fail,std::uint16_t overwrite_color) {fixture.fail_open=fail;fixture.open_failure_color=overwrite_color;}
void fail_native_keyboard_resync(bool fail) {fixture.fail_resync=fail;}
void fail_native_commit(bool fail) {fixture.fail_commit=fail;}
void signal_on_native_read(int signal) {fixture.read_signal=signal;}
void on_next_native_read(std::function<void()> callback) {fixture.read_callback=std::move(callback);}
void on_next_native_keyboard_close(std::function<void()> callback) {fixture.close_callback=std::move(callback);}
} // namespace tyrell::game::test
