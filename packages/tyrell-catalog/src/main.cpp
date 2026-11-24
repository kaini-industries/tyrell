#include "catalog_model.hpp"
#include "backend_process.hpp"
#include "artwork.hpp"
#include "lvgl.h"
#include "src/misc/cache/instance/lv_image_cache.h"
#include "src/misc/cache/instance/lv_image_header_cache.h"

#include <algorithm>
#include <array>
#include <chrono>
#include <csignal>
#include <cstdlib>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#include <poll.h>
#if defined(__linux__)
#include <fcntl.h>
#include <linux/input.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <unistd.h>
#include "src/drivers/display/fb/lv_linux_fbdev.h"
#endif

namespace {
using namespace tyrell::catalog;
constexpr int width=320,height=170;
volatile std::sig_atomic_t running=1;
void stop(int) {running=0;}
std::uint32_t tick() {return static_cast<std::uint32_t>(std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now().time_since_epoch()).count());}
struct Options {std::string screenshot,fixture,report,script,framebuffer="/dev/fb_lcd",input;unsigned frames=0;};
void usage() {
    std::cout<<"Tyrell Catalog " TYRELL_VERSION "\nSeparate normal-user discovery and verified downloads.\n"
             <<"  --headless-screenshot PPM  Render 320x170 without hardware\n"
             <<"  --fixture TSV              Offline display fixture (headless only)\n"
             <<"  --script down,enter,...    Preview controls (headless only; no transfers)\n"
             <<"  --report JSON              Semantic layout report (headless only)\n"
             <<"  --frames N                 Bounded rendering iterations\n"
             <<"  --framebuffer PATH         Native framebuffer (default /dev/fb_lcd)\n"
             <<"  --input PATH               Native evdev keyboard\n"
             <<"Keyboard: Up/Down select; Enter details; R refresh; D download; A artwork; C cancel; H help; Q/ESC back/exit.\n"
             <<"Opening the catalog reads local data only. Install, remove and repair remain in Store.\n";
}
Options arguments(int argc,char** argv) {
    Options result;
    for(int i=1;i<argc;++i) {
        const std::string argument=argv[i];
        auto value=[&](){if(++i>=argc)throw std::runtime_error("Missing value for "+argument);return std::string(argv[i]);};
        if(argument=="--help"){usage();std::exit(0);}
        else if(argument=="--version"){std::cout<<"tyrell-catalog " TYRELL_VERSION " / " TYRELL_REVISION "\n";std::exit(0);}
        else if(argument=="--headless-screenshot")result.screenshot=value();
        else if(argument=="--fixture")result.fixture=value();
        else if(argument=="--report")result.report=value();
        else if(argument=="--script")result.script=value();
        else if(argument=="--framebuffer")result.framebuffer=value();
        else if(argument=="--input")result.input=value();
        else if(argument=="--frames") {const auto text=value();std::size_t used=0;const auto frames=std::stoul(text,&used);if(used!=text.size()||frames<1||frames>10000)throw std::runtime_error("Frames must be 1..10000");result.frames=static_cast<unsigned>(frames);}
        else throw std::runtime_error("Unknown option: "+argument);
    }
    if(result.screenshot.empty()&&(!result.fixture.empty()||!result.report.empty()||!result.script.empty()))
        throw std::runtime_error("Fixture, script and report options require headless rendering");
    if(result.script.size()>2048)throw std::runtime_error("Preview script is too large");
    return result;
}
struct Element {std::string role;lv_obj_t* object;};
struct Ui {std::vector<Element> elements;std::vector<std::uint8_t> artwork_bytes;lv_image_dsc_t artwork{};std::string artwork_sha;};
constexpr std::uint32_t ink=0x24264b,paper=0xf5eedb,blue=0x294ca6,mint=0x9bd8b0,gold=0xf4bc52,muted=0x596074,coral=0xb94e47;
lv_obj_t* box(lv_obj_t* parent,int x,int y,int w,int h,std::uint32_t color) {
    auto* result=lv_obj_create(parent);lv_obj_set_pos(result,x,y);lv_obj_set_size(result,w,h);
    lv_obj_remove_flag(result,LV_OBJ_FLAG_SCROLLABLE);lv_obj_set_style_bg_opa(result,LV_OPA_COVER,0);
    lv_obj_set_style_bg_color(result,lv_color_hex(color),0);lv_obj_set_style_border_width(result,0,0);
    lv_obj_set_style_radius(result,0,0);lv_obj_set_style_pad_all(result,0,0);return result;
}
lv_obj_t* label(Ui& ui,lv_obj_t* parent,const std::string& role,const std::string& value,int x,int y,int w,int h,std::uint32_t color=ink,bool small=true,bool wrap=false) {
    auto* object=lv_label_create(parent);lv_label_set_text(object,value.c_str());lv_obj_set_pos(object,x,y);lv_obj_set_size(object,w,h);
    lv_label_set_long_mode(object,wrap?LV_LABEL_LONG_MODE_WRAP:LV_LABEL_LONG_MODE_DOTS);
    lv_obj_set_style_text_font(object,small?&lv_font_montserrat_12:&lv_font_montserrat_14,0);
    lv_obj_set_style_text_color(object,lv_color_hex(color),0);ui.elements.push_back({role,object});return object;
}
void draw(Ui& ui,const Model& model) {
    auto* screen=lv_screen_active();lv_obj_clean(screen);ui.elements.clear();
    lv_obj_remove_flag(screen,LV_OBJ_FLAG_SCROLLABLE);lv_obj_set_style_bg_color(screen,lv_color_hex(paper),0);
    lv_obj_set_style_bg_opa(screen,LV_OPA_COVER,0);lv_obj_set_style_pad_all(screen,0,0);lv_obj_set_style_border_width(screen,0,0);
    box(screen,0,0,width,25,ink);
    box(screen,8,7,5,11,gold);box(screen,5,10,11,5,gold);
    label(ui,screen,"title","TYRELL CATALOG",23,5,224,18,paper,false);
    const auto count=model.snapshot.packages.empty()?"0 apps":std::to_string(model.selected+1)+"/"+std::to_string(model.snapshot.packages.size());
    label(ui,screen,"count",model.busy?"WORKING":count,249,7,64,14,mint);
    label(ui,screen,"freshness",model.state_label(),10,29,300,15,model.snapshot.state=="fresh"?blue:coral);
    if(model.page==Page::List) {
        if(model.snapshot.packages.empty()) {
            label(ui,screen,"empty-title","Your pocket catalog",12,52,296,20,ink,false);
            label(ui,screen,"empty-help","Press R to check the configured source.\nOpening this screen stays offline.",12,79,296,34,muted,true,true);
        } else {
            const auto first=(model.selected/3)*3;
            for(std::size_t row=0;row<3 && first+row<model.snapshot.packages.size();++row) {
                const auto index=first+row;const auto& item=model.snapshot.packages[index];const auto y=47+int(row)*24;
                const bool focused=index==model.selected;
                auto* background=box(screen,7,y,306,22,focused?blue:paper);
                if(focused){lv_obj_set_style_border_color(background,lv_color_hex(gold),0);lv_obj_set_style_border_width(background,2,0);}
                const auto color=focused?paper:ink;
                label(ui,screen,"package-title-"+std::to_string(row),item.title,14,y+4,164,15,color);
                label(ui,screen,"package-version-"+std::to_string(row),item.version,180,y+4,77,15,color);
                label(ui,screen,"package-size-"+std::to_string(row),size_text(item.bytes),259,y+4,48,15,color);
            }
        }
    } else if(model.page==Page::Details && model.current()) {
        const auto& item=*model.current();
        bool shown=false;
        if(item.artwork) {
            try {
                if(ui.artwork_sha!=item.artwork->sha256) {
                    auto bytes=load_artwork(*item.artwork);
                    lv_image_cache_drop(&ui.artwork);lv_image_header_cache_drop(&ui.artwork);
                    ui.artwork_bytes=std::move(bytes);ui.artwork={};
                    ui.artwork.header.magic=LV_IMAGE_HEADER_MAGIC;ui.artwork.header.cf=LV_COLOR_FORMAT_RAW_ALPHA;
                    ui.artwork.header.w=item.artwork->width;ui.artwork.header.h=item.artwork->height;
                    ui.artwork.data_size=ui.artwork_bytes.size();ui.artwork.data=ui.artwork_bytes.data();ui.artwork_sha=item.artwork->sha256;
                }
                auto* image=lv_image_create(screen);lv_image_set_src(image,&ui.artwork);
                lv_obj_set_pos(image,12,53);lv_obj_set_size(image,64,64);
                lv_image_set_inner_align(image,LV_IMAGE_ALIGN_CONTAIN);
                ui.elements.push_back({"artwork",image});shown=true;
            } catch(const std::exception&) { /* A missing/changed icon never hides verified text. */ }
        }
        if(!shown) {box(screen,12,54,64,64,mint);label(ui,screen,"artwork-placeholder","APP",24,78,42,18,ink,false);}
        label(ui,screen,"detail-title",item.title,86,48,224,20,ink,false);
        label(ui,screen,"detail-version",item.version+" / "+size_text(item.bytes),86,70,224,15,blue);
        label(ui,screen,"detail-summary",item.summary,86,89,224,30,ink,true,true);
    } else {
        label(ui,screen,"help-navigation","Up/Down  Select   Enter  Details",12,48,296,15);
        label(ui,screen,"help-refresh","R  Refresh signed catalog",12,68,296,15);
        label(ui,screen,"help-download","D  Download   C  Cancel transfer",12,88,296,15);
        label(ui,screen,"help-store","A Artwork    Store handles installation",12,108,296,15,blue);
    }
    const std::string notice=model.notice.empty()?model.snapshot.message:model.notice;
    label(ui,screen,"notice",notice,10,124,300,27,muted,true,true);
    box(screen,0,152,width,18,ink);
    std::string footer;
    if(model.busy)footer="C Cancel    Q Back / exit";
    else if(model.page==Page::Help)footer="H Help    Q Back";
    else if(model.page==Page::Details)footer=model.can_download()?"D Download   A Artwork   Q Back":"R Refresh    Q Back    Browse only";
    else footer="Up/Down Select   Enter Info   R Refresh";
    label(ui,screen,"footer",footer,9,155,302,14,paper);
}
std::string json(const std::string& value) {
    std::string result="\"";for(const char c:value){if(c=='"'||c=='\\')result+='\\';result+=c;}return result+'"';
}
void report(const std::string& path,const Model& model,const Ui& ui) {
    std::ofstream output(path);if(!output)throw std::runtime_error("Cannot open layout report");
    output<<"{\"width\":320,\"height\":170,\"page\":"<<int(model.page)<<",\"selected\":"<<model.selected
          <<",\"state\":"<<json(model.snapshot.state)<<",\"download_enabled\":"<<(model.can_download()?"true":"false")<<",\"elements\":[";
    bool first=true;
    for(const auto& element:ui.elements) {
        if(!first){output<<',';}
        first=false;
        lv_area_t area{};lv_obj_get_coords(element.object,&area);
        if(area.x1<0 || area.y1<0 || area.x2>=width || area.y2>=height)throw std::runtime_error("Catalog layout left the LCD bounds");
        output<<"{\"role\":"<<json(element.role)<<",\"x\":"<<area.x1<<",\"y\":"<<area.y1<<",\"width\":"<<area.x2-area.x1+1<<",\"height\":"<<area.y2-area.y1+1<<'}';
    }
    output<<"]}\n";if(!output)throw std::runtime_error("Cannot write layout report");
}
void ppm(const std::string& path,const std::vector<std::uint32_t>& pixels) {
    std::ofstream output(path,std::ios::binary);if(!output)throw std::runtime_error("Cannot open screenshot");
    output<<"P6\n320 170\n255\n";
    for(const auto pixel:pixels){const char bytes[]={char(pixel>>16),char(pixel>>8),char(pixel)};output.write(bytes,3);}
    if(!output)throw std::runtime_error("Cannot write screenshot");
}
std::string fixture(const std::string& path) {
    if(!std::filesystem::is_regular_file(path) || std::filesystem::file_size(path)>65536)throw std::runtime_error("Invalid display fixture");
    std::ifstream input(path);std::ostringstream data;data<<input.rdbuf();if(!input)throw std::runtime_error("Cannot read display fixture");return data.str();
}
int script_key(const std::string& token) {
    if(token=="up")return key_up;
    if(token=="down")return key_down;
    if(token=="enter")return key_enter;
    if(token=="esc")return key_escape;
    if(token.size()==1)return token[0];
    throw std::runtime_error("Unknown preview key");
}
#if defined(__linux__)
class Keyboard {
public:
    ~Keyboard(){if(fd_>=0)::close(fd_);}
    int fd()const{return fd_;}
    bool acquire(const std::string& requested) {
        if(!requested.empty())return open_path(requested,false);
        if(open_path("/dev/input/cardputer-zero-internal",true))return true;
        std::error_code error;
        for(std::filesystem::directory_iterator it("/dev/input",error),end;!error&&it!=end;it.increment(error))
            if(it->path().filename().string().rfind("event",0)==0&&open_path(it->path().string(),true))return true;
        return false;
    }
    std::vector<int> keys() {
        std::vector<int> result;input_event event{};
        for(unsigned batch=0;batch<64;++batch) {
            const auto count=::read(fd_,&event,sizeof(event));
            if(count<0&&(errno==EAGAIN||errno==EWOULDBLOCK||errno==EINTR))break;
            if(count!=sizeof(event))throw std::runtime_error("Keyboard disconnected");
            if(event.type==EV_SYN&&event.code==SYN_DROPPED){syncing_=true;continue;}
            if(syncing_){if(event.type==EV_SYN&&event.code==SYN_REPORT){if(!baseline())throw std::runtime_error("Cannot resynchronize keyboard");}continue;}
            if(event.type!=EV_KEY||event.code>=held_.size())continue;
            if(event.value==0){held_[event.code]=false;continue;}
            if(event.value!=1||held_[event.code])continue;
            held_[event.code]=true;
            switch(event.code) {
                case KEY_UP:case KEY_K:result.push_back(key_up);break;case KEY_DOWN:case KEY_J:result.push_back(key_down);break;
                case KEY_ENTER:result.push_back(key_enter);break;case KEY_ESC:result.push_back(key_escape);break;
                case KEY_R:result.push_back('r');break;case KEY_D:result.push_back('d');break;case KEY_A:result.push_back('a');break;case KEY_C:result.push_back('c');break;
                case KEY_H:result.push_back('h');break;case KEY_Q:result.push_back('q');break;default:break;
            }
        }
        return result;
    }
private:
    int fd_=-1;std::array<bool,768> held_{};bool syncing_=false;
    bool baseline(){
        unsigned char bits[(KEY_MAX+8)/8]{};
        if(::ioctl(fd_,EVIOCGKEY(sizeof(bits)),bits)<0)return false;
        held_.fill(false);
        for(unsigned k=0;k<=KEY_MAX&&k<held_.size();++k)held_[k]=(bits[k/8]&(1U<<(k%8)))!=0;
        syncing_=false;return true;
    }
    bool open_path(const std::string& path,bool check_name) {
        const int file=::open(path.c_str(),O_RDONLY|O_NONBLOCK|O_CLOEXEC);if(file<0)return false;
        struct stat status{};char name[256]{};
        if(::fstat(file,&status)<0||!S_ISCHR(status.st_mode)||::ioctl(file,EVIOCGNAME(sizeof(name)),name)<0||
           (check_name&&std::string(name).find("tca8418")==std::string::npos)){::close(file);return false;}
        fd_=file;input_event stale{};unsigned count=0;while(count<256&&::read(fd_,&stale,sizeof(stale))==sizeof(stale))++count;
        if(count==256||!baseline()){::close(fd_);fd_=-1;return false;}return true;
    }
};
#endif
int gui(const Options& options) {
    const bool headless=!options.screenshot.empty();
    // No lifecycle/seat contract is advertised for Catalog yet. Fail before
    // creating a backend, framebuffer or keyboard for a linked native launch.
    if(!headless)if(const char* link=std::getenv("GH0ST_SHELL_FD");link&&*link)
        throw std::runtime_error("Catalog shell-linked launching is not qualified");
    std::signal(SIGINT,stop);std::signal(SIGTERM,stop);std::signal(SIGCHLD,SIG_DFL);
    Model model;BackendProcess backend;
    if(!options.fixture.empty())model.replace(parse_view(fixture(options.fixture)));
    else {backend.start(Operation::View);model.busy=true;model.notice="Reading local catalog...";}
    std::istringstream scripted(options.script);std::string token;
    while(std::getline(scripted,token,',')) {
        const auto request=model.key(script_key(token));
        if(request==Request::Refresh||request==Request::Download||request==Request::Artwork)model.notice="Preview only; no network operation started";
    }
#if defined(__linux__)
    Keyboard keyboard;
    if(!headless&&!keyboard.acquire(options.input))throw std::runtime_error("Cannot open Cardputer keyboard");
#else
    if(!headless)throw std::runtime_error("Native Catalog requires Linux; use a headless preview");
#endif
    lv_init();lv_tick_set_cb(tick);lv_display_t* display=nullptr;std::vector<std::uint32_t> pixels;
    if(headless) {
        pixels.resize(width*height);display=lv_display_create(width,height);lv_display_set_color_format(display,LV_COLOR_FORMAT_XRGB8888);
        lv_display_set_buffers(display,pixels.data(),nullptr,pixels.size()*sizeof(pixels[0]),LV_DISPLAY_RENDER_MODE_DIRECT);
        lv_display_set_flush_cb(display,[](lv_display_t* d,const lv_area_t*,std::uint8_t*){lv_display_flush_ready(d);});
    }
#if defined(__linux__)
    else {display=lv_linux_fbdev_create();if(display&&lv_linux_fbdev_set_file(display,options.framebuffer.c_str())!=LV_RESULT_OK){lv_display_delete(display);display=nullptr;}}
#endif
    if(!display){lv_deinit();throw std::runtime_error("Cannot initialize Catalog display");}
    if(lv_display_get_horizontal_resolution(display)!=width||lv_display_get_vertical_resolution(display)!=height){lv_display_delete(display);lv_deinit();throw std::runtime_error("Catalog requires a 320x170 display");}
    Ui ui;bool dirty=true,exiting=false;unsigned frame=0;const auto limit=options.frames?options.frames:(headless?2U:0U);
    [[maybe_unused]] auto request=[&](Request action) {
        if(action==Request::Exit){exiting=true;backend.cancel();return;}
        if(action==Request::Cancel){backend.cancel();model.notice="Cancelling operation...";return;}
        if(action==Request::Refresh||action==Request::Download||action==Request::Artwork) {
            if(headless){model.notice="Preview only; no network operation started";return;}
            backend.start(action==Request::Refresh?Operation::Refresh:(action==Request::Artwork?Operation::Artwork:Operation::Download),model.current()?model.current()->id:std::string{});
            model.busy=true;model.notice=action==Request::Refresh?"Refreshing signed catalog...":(action==Request::Artwork?"Caching verified artwork...":"Downloading verified archive...");
        }
    };
    try {
        while(!exiting || backend.active()) {
            if(!running&&!exiting){exiting=true;backend.cancel();}
            // Normal display operation notices expiry and wall-clock failure
            // without starting a subprocess or contacting the network. Explicit
            // preview fixtures keep their selected state deterministic.
            if(options.fixture.empty() && model.revalidate_time(static_cast<std::int64_t>(std::time(nullptr))))dirty=true;
#if defined(__linux__)
            if(!headless&&!exiting)for(const auto code:keyboard.keys()){request(model.key(code));dirty=true;if(exiting)break;}
#endif
            Completion completed;
            if(backend.poll(completed)) {
                model.busy=false;
                if(completed.operation==Operation::View) {
                    if(completed.exit_code==0&&completed.error.empty()) {
                        try{model.replace(parse_view(completed.output));if(model.notice=="Reading local catalog...")model.notice.clear();}
                        catch(const std::exception& error){model.invalidate_view(error.what());}
                    } else {
                        std::string message=completed.error.empty()?"Local verification failed; browse only":completed.error;
                        if(completed.error.empty()) {
                            try {const auto diagnostic=parse_view(completed.output);if(diagnostic.state=="error"&&!diagnostic.message.empty())message=diagnostic.message;}
                            catch(const std::exception&) { /* Never accept failed verification rows as fresh data. */ }
                        }
                        model.invalidate_view(message);
                    }
                    if(options.fixture.empty())model.revalidate_time(static_cast<std::int64_t>(std::time(nullptr)));
                } else {
                    model.notice=completed.error.empty()?parse_result(completed.output,completed.exit_code==0):completed.error;
                    if(!exiting){backend.start(Operation::View);model.busy=true;}
                }
                dirty=true;
            }
            if(exiting){if(!backend.active())break;}
            else {
                if(dirty){draw(ui,model);dirty=false;lv_refr_now(display);}
                lv_timer_handler();if(headless)lv_refr_now(display);++frame;
                if(limit&&frame>=limit&&!backend.active())break;
            }
            pollfd descriptors[2]{};nfds_t count=0;
            if(backend.fd()>=0)descriptors[count++]={backend.fd(),POLLIN,0};
#if defined(__linux__)
            if(!headless&&!exiting)descriptors[count++]={keyboard.fd(),POLLIN,0};
#endif
            ::poll(descriptors,count,headless?5:100);
        }
        if(headless){if(dirty){draw(ui,model);lv_refr_now(display);}ppm(options.screenshot,pixels);if(!options.report.empty())report(options.report,model,ui);}
    } catch(...) {lv_display_delete(display);lv_deinit();throw;}
    lv_display_delete(display);lv_deinit();return 0;
}
}
int main(int argc,char** argv) {
    try{return gui(arguments(argc,argv));}
    catch(const std::exception& error){std::cerr<<"tyrell-catalog: "<<error.what()<<'\n';return 1;}
}
