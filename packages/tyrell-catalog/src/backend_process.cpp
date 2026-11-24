#include "backend_process.hpp"

#include <cerrno>
#include <csignal>
#include <fcntl.h>
#include <stdexcept>
#include <system_error>
#include <thread>
#include <unistd.h>
#include <sys/wait.h>
#ifdef __linux__
#include <sys/syscall.h>
#endif

namespace tyrell::catalog {
namespace {
using Clock=std::chrono::steady_clock;
void child_descriptors() {
#ifdef SYS_close_range
    if(::syscall(SYS_close_range,3U,~0U,0U)==0) return;
#endif
    // Includes native framebuffer descriptors opened by third-party drivers.
    const long limit=::sysconf(_SC_OPEN_MAX);
    if(limit<0) _exit(126);
    for(long fd=3;fd<limit;++fd) ::close(static_cast<int>(fd));
}
}
BackendProcess::~BackendProcess() {
    if(pid_>0) {
        ::kill(-pid_,SIGKILL);
        int status;
        while(::waitpid(pid_,&status,0)<0 && errno==EINTR) {}
    }
    close_pipe();
}
void BackendProcess::close_pipe() {if(fd_>=0)::close(fd_);fd_=-1;}
void BackendProcess::spawn(std::vector<std::string> argv,std::chrono::milliseconds timeout) {
    if(active()) throw std::logic_error("A catalog operation is already running");
    if(argv.empty()) throw std::logic_error("Missing fixed backend command");
    int pipe[2];
    if(::pipe(pipe)<0) throw std::system_error(errno,std::generic_category(),"Catalog backend pipe");
    for(const auto fd:pipe) {
        if(::fcntl(fd,F_SETFD,FD_CLOEXEC)<0) {
            const auto error=errno;::close(pipe[0]);::close(pipe[1]);
            throw std::system_error(error,std::generic_category(),"Catalog backend descriptor");
        }
    }
    const auto child=::fork();
    if(child<0) {const auto error=errno;::close(pipe[0]);::close(pipe[1]);throw std::system_error(error,std::generic_category(),"Catalog backend start");}
    if(child==0) {
        if(::setpgid(0,0)<0) _exit(126);
        std::signal(SIGINT,SIG_DFL);std::signal(SIGTERM,SIG_DFL);
        const int null=::open("/dev/null",O_RDWR);
        if(null<0 || ::dup2(null,STDIN_FILENO)<0 || ::dup2(null,STDERR_FILENO)<0 || ::dup2(pipe[1],STDOUT_FILENO)<0) _exit(126);
        child_descriptors();
        std::vector<char*> values;for(auto& item:argv)values.push_back(item.data());values.push_back(nullptr);
        ::execv(values[0],values.data());_exit(127);
    }
    ::close(pipe[1]);
    if(::setpgid(child,child)<0 && errno!=EACCES && errno!=ESRCH) {
        const auto error=errno;
        // Darwin can report EPERM when the child has already established its
        // group or completed a short command. Observe, without reaping, rather
        // than treating that benign race as a failed launch or signalling a
        // potentially reused PID after reaping.
        siginfo_t completed{};
        const bool settled=error==EPERM && (::getpgid(child)==child ||
            (::waitid(P_PID,static_cast<id_t>(child),&completed,WEXITED|WNOHANG|WNOWAIT)==0 && completed.si_pid==child));
        if(!settled) {
            ::kill(child,SIGKILL);::close(pipe[0]);int status;while(::waitpid(child,&status,0)<0&&errno==EINTR){}
            throw std::system_error(error,std::generic_category(),"Catalog backend process group");
        }
    }
    if(::fcntl(pipe[0],F_SETFL,O_NONBLOCK)<0) {
        const auto error=errno;::kill(-child,SIGKILL);::close(pipe[0]);int status;while(::waitpid(child,&status,0)<0&&errno==EINTR){}
        throw std::system_error(error,std::generic_category(),"Catalog backend polling");
    }
    pid_=child;fd_=pipe[0];cancelling_=false;output_.clear();error_.clear();deadline_=Clock::now()+timeout;
}
void BackendProcess::start(Operation operation,const std::string& id) {
    std::vector<std::string> argv={"/usr/bin/python3","-I","/usr/libexec/tyrell-catalog/backend.py"};
    if(operation==Operation::View)argv.push_back("view");
    else if(operation==Operation::Refresh)argv.push_back("refresh");
    else {argv.push_back(operation==Operation::Artwork?"artwork":"download");argv.push_back("--id");argv.push_back(id);}
    operation_=operation;
    spawn(std::move(argv),operation==Operation::View?std::chrono::seconds(3):std::chrono::seconds(30));
}
#ifdef TYRELL_CATALOG_PROCESS_TEST
void BackendProcess::test_start(const std::vector<std::string>& argv,std::chrono::milliseconds timeout) {
    operation_=Operation::Refresh;spawn(argv,timeout);
}
#endif
void BackendProcess::cancel() {
    if(!active() || cancelling_)return;
    cancelling_=true;error_="Operation cancelled";
    ::kill(-pid_,SIGTERM);deadline_=Clock::now()+std::chrono::milliseconds(500);
}
bool BackendProcess::poll(Completion& completion) {
    if(!active())return false;
    char buffer[4096];
    for(unsigned batch=0;batch<32 && fd_>=0;++batch) {
        const auto count=::read(fd_,buffer,sizeof(buffer));
        if(count>0) {
            if(output_.size()+std::size_t(count)>65536) {
                cancel();error_="Catalog backend response exceeded its limit";close_pipe();break;
            }
            output_.append(buffer,std::size_t(count));
        } else if(count==0) {close_pipe();break;}
        else if(errno==EAGAIN || errno==EWOULDBLOCK)break;
        else if(errno!=EINTR) {cancel();error_="Unable to read catalog backend response";close_pipe();break;}
    }
    if(Clock::now()>=deadline_) {
        if(!cancelling_) {cancel();error_="Catalog operation timed out";}
        else {::kill(-pid_,SIGKILL);deadline_=Clock::now()+std::chrono::milliseconds(100);}
    }
    siginfo_t info{};
    const auto observed=::waitid(P_PID,static_cast<id_t>(pid_),&info,WEXITED|WNOHANG|WNOWAIT);
    if((observed==0 && info.si_pid==0) || (observed<0 && errno==EINTR))return false;
    // Keep the leader as a zombie while cleaning its group: its PID cannot be
    // reused by an unrelated process before the group signal. A crashed Python
    // supervisor must not leave a DNS/download worker holding the cache lock.
    if(observed==0)::kill(-pid_,SIGKILL);
    int status=0;pid_t reaped;
    do {reaped=::waitpid(pid_,&status,WNOHANG);} while(reaped<0 && errno==EINTR);
    // A child may write and exit between the first EAGAIN and waitpid. Drain
    // its remaining bounded response after observing exit before closing it.
    for(unsigned batch=0;batch<32 && fd_>=0;++batch) {
        const auto count=::read(fd_,buffer,sizeof(buffer));
        if(count>0) {
            if(output_.size()+std::size_t(count)>65536) {error_="Catalog backend response exceeded its limit";break;}
            output_.append(buffer,std::size_t(count));
        } else if(count<0 && errno==EINTR)continue;
        else break;
    }
    completion={operation_,output_,error_,reaped!=pid_?1:(WIFEXITED(status)?WEXITSTATUS(status):128+WTERMSIG(status))};
    pid_=-1;close_pipe();return true;
}
} // namespace tyrell::catalog
