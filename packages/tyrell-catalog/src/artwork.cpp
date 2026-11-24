#include "artwork.hpp"
#include <array>
#include <cerrno>
#include <fcntl.h>
#include <iomanip>
#include <sstream>
#include <stdexcept>
#include <sys/stat.h>
#include <unistd.h>
#include <openssl/evp.h>

namespace tyrell::catalog {
std::vector<std::uint8_t> load_artwork(const Artwork& descriptor) {
    if(descriptor.bytes<33 || descriptor.bytes>65536 || descriptor.width<1 || descriptor.width>128 || descriptor.height<1 || descriptor.height>128)
        throw std::runtime_error("Artwork exceeds the display limits");
    const int fd=::open(descriptor.path.c_str(),O_RDONLY|O_NONBLOCK|O_NOFOLLOW|O_CLOEXEC);
    if(fd<0)throw std::runtime_error("Artwork unavailable");
    struct File {int value;~File(){::close(value);}} file{fd};
    struct stat status{};
    if(::fstat(fd,&status)<0 || !S_ISREG(status.st_mode) || status.st_size!=descriptor.bytes)
        throw std::runtime_error("Artwork is not the expected regular file");
    std::vector<std::uint8_t> bytes(descriptor.bytes);
    std::size_t offset=0;
    while(offset<bytes.size()) {
        const auto count=::read(fd,bytes.data()+offset,bytes.size()-offset);
        if(count<0&&errno==EINTR)continue;
        if(count<=0)throw std::runtime_error("Artwork changed while reading");
        offset+=std::size_t(count);
    }
    unsigned char extra;
    if(::read(fd,&extra,1)!=0)throw std::runtime_error("Artwork size changed");
    const std::array<std::uint8_t,8> signature={137,80,78,71,13,10,26,10};
    if(!std::equal(signature.begin(),signature.end(),bytes.begin()) ||
       bytes[8]!=0 || bytes[9]!=0 || bytes[10]!=0 || bytes[11]!=13 ||
       std::string(reinterpret_cast<char*>(bytes.data()+12),4)!="IHDR")
        throw std::runtime_error("Artwork is not a bounded PNG");
    auto big=[&](unsigned at){return (std::uint32_t(bytes[at])<<24)|(std::uint32_t(bytes[at+1])<<16)|(std::uint32_t(bytes[at+2])<<8)|bytes[at+3];};
    if(big(16)!=descriptor.width || big(20)!=descriptor.height)
        throw std::runtime_error("Artwork dimensions changed");
    unsigned char hash[EVP_MAX_MD_SIZE]{};unsigned length=0;
    if(EVP_Digest(bytes.data(),bytes.size(),hash,&length,EVP_sha256(),nullptr)!=1 || length!=32)
        throw std::runtime_error("Artwork hash unavailable");
    std::ostringstream encoded;encoded<<std::hex<<std::setfill('0');
    for(unsigned i=0;i<length;++i)encoded<<std::setw(2)<<unsigned(hash[i]);
    if(encoded.str()!=descriptor.sha256)throw std::runtime_error("Artwork checksum changed");
    return bytes;
}
}
