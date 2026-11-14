#pragma once
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>
#include <vector>

namespace tyrell::game::net {

// Explicit network-byte-order scalars. No native structs, padding or pointer
// layouts appear on the wire. Consumers own their message/schema identifiers.
class ByteWriter {
public:
    explicit ByteWriter(std::size_t limit=1200) : limit_(limit) { bytes_.reserve(limit); }
    void u8(std::uint8_t value) {
        if(!good_ || bytes_.size()>=limit_) { good_=false; return; }
        bytes_.push_back(value);
    }
    void u16(std::uint16_t value) { u8(value>>8);u8(value&255); }
    void u32(std::uint32_t value) { u16(value>>16);u16(value&65535); }
    void f32(float value) {
        static_assert(sizeof(float)==4 && std::numeric_limits<float>::is_iec559,
                      "The network codec requires IEEE-754 binary32 floats");
        if(!std::isfinite(value)) { good_=false; return; }
        std::uint32_t bits;std::memcpy(&bits,&value,sizeof(bits));u32(bits);
    }
    bool good() const { return good_; }
    const std::vector<std::uint8_t>& data() const { return bytes_; }
private:
    std::size_t limit_;
    bool good_=true;
    std::vector<std::uint8_t> bytes_;
};

// Borrowed input; its storage must outlive the reader. Failed/truncated reads
// return zero and latch an error. Commit decoded application state only after
// finished() succeeds and all application-specific ranges have been checked.
class ByteReader {
public:
    ByteReader(const std::uint8_t* bytes,std::size_t size) : bytes_(bytes),size_(size),good_(bytes || size==0) {}
    explicit ByteReader(const std::vector<std::uint8_t>& bytes) : ByteReader(bytes.data(),bytes.size()) {}
    ByteReader(std::vector<std::uint8_t>&&)=delete;
    ByteReader(const std::vector<std::uint8_t>&&)=delete;
    std::uint8_t u8() {
        if(!good_ || at_>=size_) { good_=false;return 0; }
        return bytes_[at_++];
    }
    std::uint16_t u16() { const auto a=u8(),b=u8();return good_?((std::uint16_t(a)<<8)|b):0; }
    std::uint32_t u32() { const auto a=u16(),b=u16();return good_?((std::uint32_t(a)<<16)|b):0; }
    float f32() {
        static_assert(sizeof(float)==4 && std::numeric_limits<float>::is_iec559,
                      "The network codec requires IEEE-754 binary32 floats");
        const auto bits=u32();float value;std::memcpy(&value,&bits,sizeof(value));
        if(!std::isfinite(value)) { good_=false;return 0; }
        return value;
    }
    bool good() const { return good_; }
    bool finished() const { return good_ && at_==size_; }
    std::size_t size() const { return size_; }
    std::size_t remaining() const { return at_<=size_?size_-at_:0; }
private:
    const std::uint8_t* bytes_;
    std::size_t size_,at_=0;
    bool good_;
};

inline bool finite_between(float value,float low,float high) {
    return std::isfinite(value) && value>=low && value<=high;
}
// RFC-1982-style half-range comparison, including uint32 sequence rollover.
inline bool sequence_newer(std::uint32_t value,std::uint32_t previous) {
    return value!=previous && std::uint32_t(value-previous)<0x80000000u;
}
} // namespace tyrell::game::net
