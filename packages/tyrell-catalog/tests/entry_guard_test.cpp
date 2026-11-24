#define main catalog_application_main
#include "../src/main.cpp"
#undef main

#include <cerrno>
#include <sstream>

namespace {unsigned forks=0;}
extern "C" pid_t __wrap_fork() {++forks;errno=EPERM;return -1;}
namespace {
void denied(std::vector<std::string> values,const std::string& message) {
    std::vector<char*> argv;for(auto& value:values)argv.push_back(value.data());
    std::ostringstream errors;auto* original=std::cerr.rdbuf(errors.rdbuf());
    const auto result=catalog_application_main(static_cast<int>(argv.size()),argv.data());
    std::cerr.rdbuf(original);
    if(result!=1 || errors.str().find(message)==std::string::npos || forks!=0)
        throw std::runtime_error("Catalog entry guard did not reject before backend creation");
}
}
int main() {
    try {
        ::unsetenv("GH0ST_SHELL_FD");
        denied({"tyrell-catalog","--fixture","untrusted.tsv"},"require headless");
        denied({"tyrell-catalog","--script","r,d,a"},"require headless");
        denied({"tyrell-catalog","--backend","untrusted.py"},"Unknown option");
        ::setenv("GH0ST_SHELL_FD","7",1);
        denied({"tyrell-catalog"},"shell-linked launching is not qualified");
        ::unsetenv("GH0ST_SHELL_FD");
        std::cout<<"Preview and linked-launch guards rejected before any backend fork\n";return 0;
    } catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 1;}
}
