#pragma once

#include <atomic>
#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <vector>

namespace tyrell::video {

// These values come from the trusted coordinator, never an app packet. The
// coordinator journals each operation before invoking this worker-side API.
struct ManagedHdmiBinding {
    std::uint64_t epoch = 0;
    std::string resource, generation;
};

struct ManagedHdmiScope {
    std::uint32_t connector = 0, crtc = 0;
    std::vector<std::uint32_t> planes;
};

enum class ManagedHdmiState { New, Owned, Sealed, Releasing, Released, Unknown };

// Separate from legacy HdmiSession: there is deliberately no saved desktop
// snapshot or destructor restoration path. D3 does not enable live admission.
// Methods and destruction belong to one worker thread. Only state() may be
// read concurrently; the authority/control loop lives outside this worker.
class ManagedHdmiSession {
public:
    // An out-fence/readback is not generic driver cleanup finality. A qualified
    // backend must separately attest terminal completion, including all work
    // triggered by neutralization and master/FD release. Missing proof refuses
    // successful evidence. D3 fixtures provide this hook; target qualification
    // and real graph proofs remain later integration work.
    using CompletionHook = std::function<std::string(const ManagedHdmiBinding&, const ManagedHdmiScope&)>;

    // Transfer exclusive ownership of a trusted primary-DRM FD after validation.
    // No device paths are opened and no master is acquired automatically.
    ManagedHdmiSession(int fd, ManagedHdmiScope scope, ManagedHdmiBinding binding,
                       CompletionHook terminal_completion = {});
    ~ManagedHdmiSession();
    ManagedHdmiSession(const ManagedHdmiSession&) = delete;
    ManagedHdmiSession& operator=(const ManagedHdmiSession&) = delete;

    bool acquire(const std::string& operation, std::string& error);
    bool register_borrower(const std::string& borrower, std::string& error);
    // Returns an owned CLOEXEC duplicate for this graph. Closing it does not
    // retire the registered graph/driver work or certify completion.
    int borrow_fd(const std::string& borrower, const std::string& use_operation, std::string& error);
    // A close/destructor is never a borrower drain. This opaque proof must come
    // from the trusted graph/backend coordinator, not from an app assertion.
    bool borrower_drained(const std::string& borrower, const std::string& use_operation,
                          const std::string& completion_proof, std::string& error);
    bool seal(std::string& error);
    bool release(const std::string& cleanup_operation, std::string& error);
    void abandon() noexcept;
    ManagedHdmiState state() const noexcept;
    std::string identity_json() const;
    std::string acquired_json() const;
    // Backend evidence only, never an authority-issued release receipt.
    std::string evidence_json() const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace tyrell::video
