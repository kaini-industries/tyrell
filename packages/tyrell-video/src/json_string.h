#pragma once

#include <cstddef>
#include <string>

namespace tyrell::video {

// Linux paths can contain arbitrary bytes. Preserve valid UTF-8 in reports,
// replacing invalid bytes only in the JSON representation, never in the path
// used to open the media file.
inline std::string json_string(const std::string& value) {
    constexpr char hex[] = "0123456789abcdef";
    std::string out = "\"";
    for(std::size_t i = 0; i < value.size();) {
        const auto c = static_cast<unsigned char>(value[i]);
        if(c == '"' || c == '\\') { out += '\\'; out += static_cast<char>(c); ++i; }
        else if(c < 32) {
            out += "\\u00"; out += hex[c >> 4]; out += hex[c & 15]; ++i;
        } else if(c < 128) { out += static_cast<char>(c); ++i; }
        else {
            const std::size_t length = c >= 0xc2 && c <= 0xdf ? 2 : c >= 0xe0 && c <= 0xef ? 3 : c >= 0xf0 && c <= 0xf4 ? 4 : 0;
            bool valid = length && length <= value.size() - i;
            for(std::size_t offset = 1; valid && offset < length; ++offset)
                valid = (static_cast<unsigned char>(value[i + offset]) & 0xc0) == 0x80;
            if(valid) {
                const auto second = static_cast<unsigned char>(value[i + 1]);
                valid = !(c == 0xe0 && second < 0xa0) && !(c == 0xed && second >= 0xa0) &&
                        !(c == 0xf0 && second < 0x90) && !(c == 0xf4 && second >= 0x90);
            }
            if(valid) { out.append(value, i, length); i += length; }
            else { out += "\\ufffd"; ++i; }
        }
    }
    return out + '"';
}

} // namespace tyrell::video
