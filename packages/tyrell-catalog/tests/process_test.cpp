#include "backend_process.hpp"
#include <chrono>
#include <csignal>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <thread>
#include <fcntl.h>
#include <sys/file.h>
#include <unistd.h>

using namespace tyrell::catalog;
using Clock=std::chrono::steady_clock;
#define REQUIRE(value) do {if(!(value))throw std::runtime_error(#value);} while(false)
Completion finish(BackendProcess& process,std::chrono::seconds allowance=std::chrono::seconds(4)) {
    Completion result;const auto deadline=Clock::now()+allowance;
    while(Clock::now()<deadline){if(process.poll(result))return result;std::this_thread::sleep_for(std::chrono::milliseconds(2));}
    throw std::runtime_error("Backend process did not finish within test scheduling allowance");
}
int main() {
    try {
        BackendProcess process;
        for(unsigned i=0;i<8;++i) {
            process.test_start({"/usr/bin/python3","-c","print('RESULT\\t1\\tok\\tDone')"},std::chrono::seconds(2));
            const auto result=finish(process);REQUIRE(result.exit_code==0&&result.output=="RESULT\t1\tok\tDone\n"&&result.error.empty());
        }
        process.test_start({"/usr/bin/python3","-c","import time;time.sleep(10)"},std::chrono::milliseconds(20));
        auto result=finish(process);REQUIRE(result.exit_code!=0&&result.error.find("timed out")!=std::string::npos);
        process.test_start({"/usr/bin/python3","-c","import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(10)"},std::chrono::seconds(2));
        std::this_thread::sleep_for(std::chrono::milliseconds(80));const auto cancelled=Clock::now();process.cancel();result=finish(process);
        REQUIRE(result.exit_code!=0&&result.error=="Operation cancelled"&&Clock::now()-cancelled<std::chrono::seconds(2));
        process.test_start({"/usr/bin/python3","-c","print('x'*100000)"},std::chrono::seconds(2));
        result=finish(process);REQUIRE(result.error.find("limit")!=std::string::npos&&result.output.size()<=65536);
#if defined(__linux__)
        char linux_pattern[]="/tmp/catalog-process-XXXXXX";auto* directory=::mkdtemp(linux_pattern);
#else
        char pattern[]="/private/tmp/catalog-process-XXXXXX";
        auto* directory=::mkdtemp(pattern);
#endif
        REQUIRE(directory);const auto root=std::filesystem::path(directory);
        struct Cleanup{std::filesystem::path path;~Cleanup(){std::filesystem::remove_all(path);}} cleanup{root};
        const auto lock=root/"lock",ready=root/"ready";
        const auto code=std::string("import os,time,fcntl;f=open(")+"'"+lock.string()+"','w');fcntl.flock(f,fcntl.LOCK_EX);pid=os.fork();\nif pid==0:\n open('"+ready.string()+"','w').write(str(os.getpid()));time.sleep(20)\nelse:\n while not os.path.exists('"+ready.string()+"'):time.sleep(.001)\n os._exit(9)\n";
        process.test_start({"/usr/bin/python3","-c",code},std::chrono::seconds(2));result=finish(process);
        REQUIRE(result.exit_code==9&&std::filesystem::exists(ready));
        const int fd=::open(lock.c_str(),O_RDWR);REQUIRE(fd>=0);
        bool unlocked=false;
        for(unsigned tries=0;tries<100;++tries){if(::flock(fd,LOCK_EX|LOCK_NB)==0){unlocked=true;break;}std::this_thread::sleep_for(std::chrono::milliseconds(2));}
        ::close(fd);REQUIRE(unlocked);
        // Test an inherited descriptor with CLOEXEC deliberately disabled.
        const int inherited=::open("/dev/null",O_RDONLY);REQUIRE(inherited>=3);
        process.test_start({"/usr/bin/python3","-c","import os,sys\ntry:os.fstat("+std::to_string(inherited)+");sys.exit(8)\nexcept OSError:print('closed')"},std::chrono::seconds(2));
        result=finish(process);::close(inherited);REQUIRE(result.exit_code==0&&result.output=="closed\n");
        std::cout<<"Backend exit, bounded output, cancellation, descriptor and orphan-worker tests passed\n";return 0;
    } catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 1;}
}
