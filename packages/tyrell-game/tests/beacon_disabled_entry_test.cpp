// Compile the actual entry point with shell-link disabled. The descriptor must
// remain the caller's property and ordinary M5 framebuffer restoration applies.
#define main beacon_application_main
#include "../examples/beacon_arena.cpp"
#undef main

#include <vector>
#include <sys/socket.h>
#include <fcntl.h>
#include <unistd.h>
#ifdef TYRELL_BEACON_NATIVE_TEST_IO
#include "native_io_fixture.hpp"
#endif

#define REQUIRE(value) do {if(!(value))throw std::runtime_error(std::string("Beacon disabled entry: ")+#value);} while(false)
namespace {
int entry(std::vector<std::string> values) {
    std::vector<char*> argv;for(auto& value:values)argv.push_back(value.data());
    return beacon_application_main(int(argv.size()),argv.data());
}
}
int main() {
    int descriptors[2]={-1,-1};
    const char* previous=std::getenv("GH0ST_SHELL_FD");
    const std::string saved=previous?previous:"";const bool had_previous=previous!=nullptr;
    try {
        static_assert(TYRELL_ENABLE_SHELL_LINK==0,"This target proves the default-disabled contract");
        SetTraceLogLevel(LOG_WARNING);
        REQUIRE(socketpair(AF_UNIX,SOCK_SEQPACKET|SOCK_CLOEXEC,0,descriptors)==0);
        REQUIRE(send(descriptors[0],"hello\t1",7,MSG_NOSIGNAL)==7);
        REQUIRE(setenv("GH0ST_SHELL_FD",std::to_string(descriptors[1]).c_str(),1)==0);
        const int descriptor_flags=fcntl(descriptors[1],F_GETFD),status_flags=fcntl(descriptors[1],F_GETFL);
        const auto normal=runtime_options(Options{},false);REQUIRE(normal.restore_framebuffer_on_close);
        REQUIRE(entry({"tyrell-beacon-arena","--headless","--frames","1"})==0);
#ifdef TYRELL_BEACON_NATIVE_TEST_IO
        tyrell::game::test::reset_native_fixture();
        REQUIRE(entry({"tyrell-beacon-arena","--framebuffer","/fixture/lcd","--input","/fixture/keyboard","--frames","1"})==0);
        const auto stats=tyrell::game::test::native_io_stats();
        REQUIRE(stats.commits==2&&stats.key_opens==1&&stats.key_closes==1&&stats.fb_closes==1&&stats.unmaps==1);
        REQUIRE(tyrell::game::test::foreground_pixels_equal(0x1111));
#endif
        REQUIRE(fcntl(descriptors[1],F_GETFD)==descriptor_flags&&fcntl(descriptors[1],F_GETFL)==status_flags);
        REQUIRE(getenv("GH0ST_SHELL_FD")&&std::string(getenv("GH0ST_SHELL_FD"))==std::to_string(descriptors[1]));
        char buffer[32];REQUIRE(recv(descriptors[0],buffer,sizeof(buffer),MSG_DONTWAIT)<0&&(errno==EAGAIN||errno==EWOULDBLOCK));
        close(descriptors[0]);close(descriptors[1]);
        if(had_previous)setenv("GH0ST_SHELL_FD",saved.c_str(),1);else unsetenv("GH0ST_SHELL_FD");
        std::cout<<"PASS: actual default-disabled Beacon entry leaves shell endpoint untouched and preserves ordinary device restoration\n";
        return 0;
    } catch(const std::exception& error) {
        if(descriptors[0]>=0)close(descriptors[0]);
        if(descriptors[1]>=0)close(descriptors[1]);
        if(had_previous)setenv("GH0ST_SHELL_FD",saved.c_str(),1);else unsetenv("GH0ST_SHELL_FD");
        std::cerr<<error.what()<<'\n';return 1;
    }
}
