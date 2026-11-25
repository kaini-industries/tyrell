#pragma once

// Controlled Linux syscall boundaries around the production ManagedHdmiSession.
// No DRM node is opened. Link this fixture only with the explicit --wrap list.
#include "managed_hdmi.h"
#include <xf86drm.h>
#include <xf86drmMode.h>
#include <linux/sync_file.h>
#include <sys/stat.h>
#include <sys/ioctl.h>
#include <sys/sysmacros.h>
#include <poll.h>
#include <fcntl.h>
#include <unistd.h>
#include <atomic>
#include <cstdarg>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <tuple>
#include <vector>

namespace managed_hdmi_fixture {
using namespace tyrell::video;
struct Property { std::string name; std::uint64_t value; };
struct Entry { std::uint32_t object, property; std::uint64_t value; };
struct Request { std::vector<Entry> entries; };
struct Fixture;
inline Fixture* current = nullptr;

struct Fixture {
    int fd = -1, fence_read = -1, fence_write = -1;
    bool owns_fd = true;
    bool master = true, atomic_supported = true, fail_test = false, fail_commit = false;
    bool missing_fence = false, fail_fence = false, fail_drop = false, corrupt_readback = false;
    bool hold_fence = false;
    std::atomic<int> test_calls{0}, commit_calls{0}, terminal_calls{0}, drop_calls{0};
    std::atomic<bool> terminal_entered{false};
    std::atomic<bool> fence_ready{false};
    std::function<void()> terminal_gate;
    std::string terminal_proof = std::string(32, '9');
    std::vector<Entry> mutations;
    std::map<std::uint32_t, std::map<std::uint32_t, Property>> objects{
        {11, {{101, {"CRTC_ID", 12}}}}, {13, {{102, {"CRTC_ID", 99}}}},
        {12, {{201, {"ACTIVE", 1}}, {202, {"MODE_ID", 301}}, {203, {"OUT_FENCE_PTR", 0}}}},
        {14, {{401, {"FB_ID", 500}}, {402, {"CRTC_ID", 12}}}},
        {15, {{501, {"FB_ID", 501}}, {502, {"CRTC_ID", 12}}}},
        {16, {{601, {"FB_ID", 900}}, {602, {"CRTC_ID", 99}}}}
    };

    Fixture() {
        if(current) throw std::runtime_error("Only one controlled KMS fixture per process");
        char path[] = "/tmp/tyrell-managed-kms-XXXXXX";
        fd = mkstemp(path);
        if(fd < 0) throw std::runtime_error("Cannot create controlled KMS descriptor");
        unlink(path);
        current = this;
    }
    ~Fixture() {
        if(owns_fd && fd >= 0) close(fd);
        if(fence_read >= 0) close(fence_read);
        if(fence_write >= 0) close(fence_write);
        current = nullptr;
    }
    ManagedHdmiScope scope() const { return {11, 12, {14, 15}}; }
    ManagedHdmiBinding binding() const { return {1, std::string(32,'a'), std::string(32,'b')}; }
    ManagedHdmiSession::CompletionHook completion() {
        return [this](const auto&, const auto&) {
            ++terminal_calls;
            terminal_entered = true;
            if(terminal_gate) terminal_gate();
            return terminal_proof;
        };
    }
    std::unique_ptr<ManagedHdmiSession> session(ManagedHdmiBinding identity = {1, std::string(32,'a'), std::string(32,'b')},
                                                ManagedHdmiSession::CompletionHook hook = {}) {
        auto result = std::make_unique<ManagedHdmiSession>(fd, scope(), std::move(identity), hook ? std::move(hook) : completion());
        owns_fd = false;
        return result;
    }
    void signal_fence() {
        if(fence_write < 0 || write(fence_write, "F", 1) != 1) throw std::runtime_error("Cannot signal controlled fence");
    }
    std::uint64_t value(std::uint32_t object, const std::string& name) const {
        for(const auto& item : objects.at(object)) if(item.second.name == name) return item.second.value;
        throw std::runtime_error("Unknown controlled property");
    }
    void set(std::uint32_t object, const std::string& name, std::uint64_t value) {
        for(auto& item : objects.at(object)) if(item.second.name == name) { item.second.value = value; return; }
        throw std::runtime_error("Unknown controlled property");
    }
};
inline Fixture& fixture() { if(!current) throw std::runtime_error("Missing controlled KMS fixture"); return *current; }
} // namespace managed_hdmi_fixture

extern "C" {
int __real_fstat(int, struct stat*);
int __wrap_fstat(int fd, struct stat* result) {
    const int status = __real_fstat(fd, result);
    if(status == 0 && managed_hdmi_fixture::current && fd == managed_hdmi_fixture::current->fd) {
        result->st_mode = S_IFCHR | 0600;
        result->st_rdev = makedev(226, 0);
    }
    return status;
}
int __wrap_drmIsMaster(int) { return managed_hdmi_fixture::fixture().master ? 1 : 0; }
int __wrap_drmSetClientCap(int, std::uint64_t capability, std::uint64_t value) {
    auto& f = managed_hdmi_fixture::fixture();
    return capability == DRM_CLIENT_CAP_ATOMIC && value == 1 && f.atomic_supported ? 0 : -1;
}
drmModeResPtr __wrap_drmModeGetResources(int) {
    auto* result = static_cast<drmModeResPtr>(std::calloc(1, sizeof(drmModeRes)));
    result->count_crtcs = 2;
    result->crtcs = static_cast<std::uint32_t*>(std::malloc(2*sizeof(std::uint32_t)));
    result->crtcs[0] = 12; result->crtcs[1] = 99;
    result->count_connectors = 2;
    result->connectors = static_cast<std::uint32_t*>(std::malloc(2*sizeof(std::uint32_t)));
    result->connectors[0] = 11; result->connectors[1] = 13;
    return result;
}
void __wrap_drmModeFreeResources(drmModeResPtr value) { std::free(value->crtcs); std::free(value->connectors); std::free(value); }
drmModePlaneResPtr __wrap_drmModeGetPlaneResources(int) {
    auto* result = static_cast<drmModePlaneResPtr>(std::calloc(1, sizeof(drmModePlaneRes)));
    result->count_planes = 3;
    result->planes = static_cast<std::uint32_t*>(std::malloc(3*sizeof(std::uint32_t)));
    result->planes[0] = 14; result->planes[1] = 15; result->planes[2] = 16;
    return result;
}
void __wrap_drmModeFreePlaneResources(drmModePlaneResPtr value) { std::free(value->planes); std::free(value); }
drmModeObjectPropertiesPtr __wrap_drmModeObjectGetProperties(int, std::uint32_t object, std::uint32_t) {
    auto& objects = managed_hdmi_fixture::fixture().objects;
    if(!objects.count(object)) return nullptr;
    auto* result = static_cast<drmModeObjectPropertiesPtr>(std::calloc(1, sizeof(drmModeObjectProperties)));
    result->count_props = objects.at(object).size();
    result->props = static_cast<std::uint32_t*>(std::malloc(result->count_props*sizeof(std::uint32_t)));
    result->prop_values = static_cast<std::uint64_t*>(std::malloc(result->count_props*sizeof(std::uint64_t)));
    unsigned i = 0;
    for(const auto& item : objects.at(object)) { result->props[i] = item.first; result->prop_values[i++] = item.second.value; }
    return result;
}
void __wrap_drmModeFreeObjectProperties(drmModeObjectPropertiesPtr value) {
    std::free(value->props); std::free(value->prop_values); std::free(value);
}
drmModePropertyPtr __wrap_drmModeGetProperty(int, std::uint32_t property) {
    for(const auto& object : managed_hdmi_fixture::fixture().objects) for(const auto& item : object.second) if(item.first == property) {
        auto* result = static_cast<drmModePropertyPtr>(std::calloc(1, sizeof(drmModePropertyRes)));
        result->prop_id = property;
        std::strncpy(result->name, item.second.name.c_str(), sizeof(result->name)-1);
        return result;
    }
    return nullptr;
}
void __wrap_drmModeFreeProperty(drmModePropertyPtr value) { std::free(value); }
drmModeAtomicReqPtr __wrap_drmModeAtomicAlloc() {
    return reinterpret_cast<drmModeAtomicReqPtr>(new managed_hdmi_fixture::Request);
}
int __wrap_drmModeAtomicAddProperty(drmModeAtomicReqPtr request, std::uint32_t object, std::uint32_t property, std::uint64_t value) {
    reinterpret_cast<managed_hdmi_fixture::Request*>(request)->entries.push_back({object,property,value}); return 0;
}
void __wrap_drmModeAtomicFree(drmModeAtomicReqPtr request) { delete reinterpret_cast<managed_hdmi_fixture::Request*>(request); }
int __wrap_drmModeAtomicCommit(int, drmModeAtomicReqPtr request, std::uint32_t flags, void*) {
    using namespace managed_hdmi_fixture;
    auto& f = fixture();
    auto& entries = reinterpret_cast<Request*>(request)->entries;
    int* fence = nullptr;
    for(const auto& entry : entries) if(entry.property == 203) fence = reinterpret_cast<int*>(static_cast<std::uintptr_t>(entry.value));
    if(!fence) return -1;
    *fence = -1;
    if(flags & DRM_MODE_ATOMIC_TEST_ONLY) { ++f.test_calls; return f.fail_test ? -1 : 0; }
    ++f.commit_calls;
    if(f.fail_commit) return -1;
    for(const auto& entry : entries) {
        if(entry.property == 203) continue;
        if(!f.objects.count(entry.object) || !f.objects.at(entry.object).count(entry.property)) return -1;
        f.objects.at(entry.object).at(entry.property).value = entry.value;
        f.mutations.push_back(entry);
    }
    if(!f.missing_fence) {
        int pipe[2]; if(::pipe(pipe) != 0) return -1;
        f.fence_read = pipe[0]; f.fence_write = pipe[1];
        *fence = f.fence_read;
        // The production class owns this descriptor after commit.
        f.fence_read = -1;
        f.fence_ready = true;
        if(!f.hold_fence) f.signal_fence();
    }
    return 0;
}
int __wrap_ioctl(int fd, unsigned long request, ...) {
    va_list arguments; va_start(arguments, request); void* data = va_arg(arguments, void*); va_end(arguments);
    if(request == SYNC_IOC_FILE_INFO) {
        auto& f = managed_hdmi_fixture::fixture();
        static_cast<sync_file_info*>(data)->status = f.fail_fence ? -1 : 1;
        if(f.corrupt_readback) f.set(12, "ACTIVE", 1);
        return 0;
    }
    (void)fd;
    return -1;
}
int __wrap_drmDropMaster(int) { auto& f = managed_hdmi_fixture::fixture(); ++f.drop_calls; return f.fail_drop ? -1 : 0; }
} // extern C
