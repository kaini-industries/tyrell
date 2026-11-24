#pragma once

#include <chrono>
#include <string>
#include <vector>

namespace tyrell::catalog {
enum class Operation { View, Refresh, Download, Artwork };
struct Completion {Operation operation=Operation::View;std::string output, error;int exit_code=1;};
class BackendProcess {
public:
    BackendProcess()=default;
    ~BackendProcess();
    BackendProcess(const BackendProcess&)=delete;
    BackendProcess& operator=(const BackendProcess&)=delete;
    void start(Operation operation,const std::string& id={});
    void cancel();
    bool poll(Completion& completion);
    bool active() const {return pid_>0;}
    int fd() const {return fd_;}
#ifdef TYRELL_CATALOG_PROCESS_TEST
    void test_start(const std::vector<std::string>& argv,std::chrono::milliseconds timeout);
#endif
private:
    void spawn(std::vector<std::string> argv,std::chrono::milliseconds timeout);
    void close_pipe();
    int pid_=-1,fd_=-1;
    Operation operation_=Operation::View;
    bool cancelling_=false;
    std::string output_,error_;
    std::chrono::steady_clock::time_point deadline_{};
};
} // namespace tyrell::catalog
