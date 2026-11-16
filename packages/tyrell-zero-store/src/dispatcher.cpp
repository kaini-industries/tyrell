#include "routing.hpp"
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include <unistd.h>
extern char** environ;

static bool clear_environment() {
    while (environ && environ[0]) {
        const std::string entry(environ[0]);
        if (unsetenv(entry.substr(0, entry.find('=')).c_str()) != 0) return false;
    }
    return true;
}

int main(int argc, char** argv) {
    using namespace tyrell_store;
    constexpr const char* public_path = "/usr/share/APPLaunch/bin/M5CardputerZero-AppStore";
    const auto destination = route(geteuid() == 0, argc, argv);
    if (destination == Route::reject) {
        std::fputs("ERROR\tTyrell Store requires normal-user UI and its guarded package protocol\n", stderr);
        return 126;
    }
    std::vector<char*> arguments;
    const char* executable = "/usr/share/APPLaunch/bin/M5CardputerZero-AppStore.vendor";
    if (destination == Route::helper) {
        // sudo supplies this identity; no preserved path/Python/root override exists.
        const std::string caller = std::getenv("SUDO_UID") ? std::getenv("SUDO_UID") : "";
        if (caller != "1000") {
            std::fputs("ERROR\tConfigured normal-user authorization required\n", stderr);
            return 126;
        }
        if (!clear_environment() || setenv("SUDO_UID", caller.c_str(), 1) != 0 ||
            setenv("PATH", "/usr/sbin:/usr/bin:/sbin:/bin", 1) != 0 || setenv("LANG", "C.UTF-8", 1) != 0)
            return 126;
        executable = "/usr/bin/python3";
        arguments.push_back(const_cast<char*>(executable));
        arguments.push_back(const_cast<char*>("-I"));
        arguments.push_back(const_cast<char*>("-B"));
        arguments.push_back(const_cast<char*>("/usr/libexec/tyrell-zero/appstore-package-helper"));
    } else {
        // argv[0] is part of the pinned vendor executable's self-relaunch contract.
        arguments.push_back(const_cast<char*>(public_path));
    }
    for (int i = 1; i < argc; ++i) arguments.push_back(argv[i]);
    arguments.push_back(nullptr);
    execv(executable, arguments.data());
    std::fprintf(stderr, "ERROR\tUnable to execute Tyrell Store integration: %s\n", std::strerror(errno));
    return 127;
}
