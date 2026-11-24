#include "native_backend.hpp"
#include <array>
#include <cerrno>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>

#ifdef __linux__
#include <fcntl.h>
#include <linux/fb.h>
#include <linux/input.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

namespace tyrell::game::detail {
constexpr int width=320,height=170;
namespace {
class Framebuffer {
public:
    explicit Framebuffer(const std::string& requested,bool restore_on_close):restore_on_close_(restore_on_close) {
#ifdef __linux__
        path_=requested.empty() || requested=="auto"?discover():requested;
        fd_=open(path_.c_str(),O_RDWR|O_CLOEXEC);
        if(fd_<0) throw std::runtime_error("Cannot open native LCD "+path_+": "+std::strerror(errno));
        try {
            fb_fix_screeninfo fix{}; fb_var_screeninfo var{};
            if(ioctl(fd_,FBIOGET_FSCREENINFO,&fix)<0 || ioctl(fd_,FBIOGET_VSCREENINFO,&var)<0)
                throw std::runtime_error("Cannot query native LCD framebuffer");
            if(var.xres!=width || var.yres!=height || var.bits_per_pixel!=16 ||
                var.red.offset!=11 || var.red.length!=5 || var.green.offset!=5 || var.green.length!=6 ||
                var.blue.offset!=0 || var.blue.length!=5 || var.red.msb_right || var.green.msb_right ||
                var.blue.msb_right || var.transp.length || var.grayscale || var.nonstd ||
                fix.type!=FB_TYPE_PACKED_PIXELS || fix.visual!=FB_VISUAL_TRUECOLOR)
                throw std::runtime_error("Native runtime requires the 320x170 RGB565 LCD; refusing another framebuffer");
            if(var.xres_virtual<var.xres || var.yres_virtual<var.yres ||
                var.xoffset>var.xres_virtual-var.xres || var.yoffset>var.yres_virtual-var.yres ||
                std::uint64_t(var.xoffset+var.xres)*2>fix.line_length)
                throw std::runtime_error("LCD visible region exceeds virtual geometry or stride");
            stride_=fix.line_length; length_=fix.smem_len;
            offset_=std::size_t(var.yoffset)*stride_+std::size_t(var.xoffset)*2;
            if(stride_<width*2 || offset_+std::size_t(height-1)*stride_+width*2>length_)
                throw std::runtime_error("LCD framebuffer mapping is too small");
            data_=static_cast<std::uint8_t*>(mmap(nullptr,length_,PROT_READ|PROT_WRITE,MAP_SHARED,fd_,0));
            if(data_==MAP_FAILED) {data_=nullptr;throw std::runtime_error("Cannot map native LCD");}
            if(restore_on_close_) {
                saved_.resize(width*height*2);
                for(int y=0;y<height;++y) std::memcpy(saved_.data()+y*width*2,data_+offset_+y*stride_,width*2);
            }
        } catch(...) {close();throw;}
#else
        (void)requested;
        throw std::runtime_error("Native LCD output requires Linux; use headless for smoke testing");
#endif
    }
    ~Framebuffer() {
#ifdef __linux__
        if(restore_on_close_ && foreground_ && data_ && !saved_.empty()) {
            for(int y=0;y<height;++y) std::memcpy(data_+offset_+y*stride_,saved_.data()+y*width*2,width*2);
            try {commit();} catch(const std::exception& e) {std::cerr<<"Tyrell LCD restore: "<<e.what()<<'\n';}
        }
#endif
        close();
    }
    void present(const std::vector<std::uint16_t>& rgb) {
        if(!foreground_) throw std::logic_error("Cannot present a native frame without foreground ownership");
#ifdef __linux__
        if(!data_) return;
        for(int y=0;y<height;++y) std::memcpy(data_+offset_+y*stride_,rgb.data()+y*width,width*2);
        commit(); // One completed frame, one deferred-update commit.
#else
        (void)rgb;
#endif
    }
    void foreground(bool owned) noexcept { foreground_=owned; }
private:
    bool foreground_=true;
    [[maybe_unused]] const bool restore_on_close_;
#ifdef __linux__
    int fd_=-1;
    std::uint8_t* data_=nullptr;
    std::size_t stride_=0,length_=0,offset_=0;
    std::string path_;
    std::vector<std::uint8_t> saved_;
    static std::string discover() {
        struct stat status{};
        if(stat("/dev/fb_lcd",&status)==0 && S_ISCHR(status.st_mode)) return "/dev/fb_lcd";
        std::ifstream entries("/proc/fb"); std::string line;
        while(std::getline(entries,line)) {
            std::istringstream in(line);int index=-1;std::string name;
            if(in>>index>>name && index>=0 && name.find("st7789")!=std::string::npos)
                return "/dev/fb"+std::to_string(index);
        }
        for(int index=0;index<16;++index) {
            const auto link="/sys/class/graphics/fb"+std::to_string(index)+"/device/driver";
            std::array<char,1024> target{};
            const auto count=readlink(link.c_str(),target.data(),target.size()-1);
            if(count>0 && std::string(target.data(),std::size_t(count)).find("st7789")!=std::string::npos)
                return "/dev/fb"+std::to_string(index);
        }
        throw std::runtime_error("Cannot identify native LCD; specify its verified framebuffer path");
    }
    void commit() {
        while(fsync(fd_)<0) { if(errno==EINTR) continue;throw std::runtime_error("LCD commit failed: "+std::string(std::strerror(errno))); }
    }
    void close() noexcept {
        if(data_) munmap(data_,length_);
        if(fd_>=0) ::close(fd_);
        data_=nullptr;fd_=-1;
    }
#else
    void close() noexcept {}
#endif
};

class Keyboard {
public:
    Keyboard(const std::string& requested,NativeInputState& state):requested_(requested) {
        acquire(state,false);
    }
    ~Keyboard() { close(); }
    void release(NativeInputState& state) noexcept { close();state.reset(); }
    void reopen(NativeInputState& state) { acquire(state,true); }
    void poll(NativeInputState& state) {
#ifdef __linux__
        if(fd_<0) return;
        std::array<input_event,32> events{};
        for(int batch=0;batch<32;++batch) {
            const auto count=read(fd_,events.data(),sizeof(events));
            if(count<0) {
                if(errno==EINTR) continue;
                if(errno==EAGAIN || errno==EWOULDBLOCK) break;
                throw std::runtime_error("Keyboard read failed: "+std::string(std::strerror(errno)));
            }
            if(count==0) throw std::runtime_error("Native keyboard disconnected");
            if(count%sizeof(input_event)!=0) throw std::runtime_error("Incomplete native keyboard event");
            const auto size=std::size_t(count)/sizeof(input_event);
            for(std::size_t i=0;i<size;++i) {
                const auto& event=events[i];
                if(state.sync_needed() && event.type==EV_SYN && event.code==SYN_REPORT) resync(state);
                else state.feed(event.type,event.code,event.value);
            }
        }
#else
        (void)state;
#endif
    }
private:
    std::string requested_,path_;
    [[maybe_unused]] bool check_name_=false;
    void acquire(NativeInputState& state,bool resumed) {
        close();
        if(requested_=="none") {state.reset();return;}
#ifdef __linux__
        if(!path_.empty()) open_path(path_,check_name_);
        else if(requested_.empty() || requested_=="auto") {
            if(!open_path("/dev/input/cardputer-zero-internal",true)) {
                std::error_code error;
                for(std::filesystem::directory_iterator it("/dev/input",error),end;!error && it!=end;it.increment(error)) {
                    if(it->path().filename().string().rfind("event",0)==0 && open_path(it->path().string(),true)) break;
                }
            }
        } else open_path(requested_,false);
        if(fd_<0) throw std::runtime_error("Cannot open CardputerZero keyboard (TCA8418 evdev)");
        try {
            if(resumed) discard_pending();
            resync(state,resumed);
        } catch(...) {release(state);throw;}
#else
        (void)state;(void)resumed;
        throw std::runtime_error("Native evdev input requires Linux");
#endif
    }
    void close() noexcept {
#ifdef __linux__
        if(fd_>=0) ::close(fd_);
        fd_=-1;
#endif
    }
#ifdef __linux__
    int fd_=-1;
    bool open_path(const std::string& path,bool check_name) {
        const int file=open(path.c_str(),O_RDONLY|O_NONBLOCK|O_CLOEXEC);
        if(file<0) return false;
        struct stat status{};char name[256]{};
        if(fstat(file,&status)<0 || !S_ISCHR(status.st_mode) ||
            ioctl(file,EVIOCGNAME(sizeof(name)),name)<0 ||
            (check_name && std::string(name).find("tca8418")==std::string::npos)) {::close(file);return false;}
        fd_=file;path_=path;check_name_=check_name;return true;
    }
    void discard_pending() {
        std::array<input_event,32> events{};
        for(int batch=0;batch<32;++batch) {
            const auto count=read(fd_,events.data(),sizeof(events));
            if(count<0) {
                if(errno==EINTR) continue;
                if(errno==EAGAIN || errno==EWOULDBLOCK) return;
                throw std::runtime_error("Cannot discard stale native keyboard events");
            }
            if(count==0 || count%sizeof(input_event)!=0)
                throw std::runtime_error("Native keyboard disconnected while resuming");
        }
        throw std::runtime_error("Native keyboard queue did not settle on resume");
    }
    void resync(NativeInputState& state,bool suppress_held=false) {
        constexpr std::size_t word_bits=sizeof(unsigned long)*8;
        std::array<unsigned long,(KEY_MAX+word_bits)/word_bits> bits{};
        if(ioctl(fd_,EVIOCGKEY(sizeof(bits)),bits.data())<0)
            throw std::runtime_error("Cannot resynchronize native keyboard");
        std::vector<unsigned short> held;
        for(unsigned short code=0;code<=KEY_MAX;++code)
            if(bits[code/word_bits]&(1UL<<(code%word_bits))) held.push_back(code);
        std::array<unsigned long,1> leds{};
        const bool caps=ioctl(fd_,EVIOCGLED(sizeof(leds)),leds.data())>=0 && (leds[0]&(1UL<<LED_CAPSL));
        state.resync(held,caps,suppress_held);
    }
#endif
};
}

struct NativeDevices::Impl {
    Framebuffer display;
    Keyboard keyboard;
    bool foreground=true;
    Impl(const std::string& framebuffer,const std::string& input,NativeInputState& state,bool restore_on_close)
        :display(framebuffer,restore_on_close),keyboard(input,state) {}
};
NativeDevices::NativeDevices(const std::string& framebuffer,const std::string& input,NativeInputState& state,bool restore_on_close)
    :impl_(std::make_unique<Impl>(framebuffer,input,state,restore_on_close)) {}
NativeDevices::~NativeDevices()=default;
void NativeDevices::poll(NativeInputState& state) { if(impl_->foreground) impl_->keyboard.poll(state); }
void NativeDevices::present(const std::vector<std::uint16_t>& rgb565) {
    if(rgb565.size()!=width*height) throw std::runtime_error("Invalid native frame size");
    impl_->display.present(rgb565);
}
void NativeDevices::suspend(NativeInputState& state) noexcept {
    impl_->foreground=false;
    impl_->display.foreground(false);
    impl_->keyboard.release(state);
}
bool NativeDevices::resume(NativeInputState& state,std::string* error) {
    if(impl_->foreground) return true;
    try {impl_->keyboard.reopen(state);}
    catch(const std::exception& failure) {if(error)*error=failure.what();return false;}
    impl_->foreground=true;
    impl_->display.foreground(true);
    return true;
}
void NativeDevices::abandon(NativeInputState& state) noexcept { suspend(state); }
} // namespace tyrell::game::detail
