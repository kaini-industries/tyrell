#include "json_string.h"

#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

using tyrell::video::json_string;

void require(bool value, const char* message) { if(!value) throw std::runtime_error(message); }

int main() {
    try {
        const std::string latin = u8"café \"movie\"\\.mp4";
        require(json_string(latin) == u8"\"café \\\"movie\\\"\\\\.mp4\"", "Valid UTF-8 and path punctuation must survive JSON encoding");
        // Boundary scalars cover every UTF-8 length, the surrogate gap and the
        // highest scalar. These must remain valid text rather than replacements.
        const std::string unicode = u8"\u0080\u07ff\u0800\ud7ff\ue000\uffff\U00010000\U0010ffff";
        require(json_string(unicode) == '"' + unicode + '"', "Valid Unicode boundary scalars must be preserved");
        require(json_string(std::string("a\0\n\r\t\b\f\x1f", 8)) == "\"a\\u0000\\u000a\\u000d\\u0009\\u0008\\u000c\\u001f\"", "All control bytes must be escaped without data loss");

        const std::vector<std::string> invalid = {
            "\x80", "\xbf", "\xff", "\xc0\xaf", "\xc1\xbf", // stray continuations and overlong ASCII
            "\xe0\x80\x80", "\xed\xa0\x80",                 // overlong and UTF-16 surrogate
            "\xf0\x80\x80\x80", "\xf4\x90\x80\x80",         // overlong and above U+10FFFF
            "\xf5\x80\x80\x80", "\xc2", "\xe2\x82", "\xf0\x9f\x8e" // invalid leaders and truncation
        };
        for(const auto& bytes : invalid) {
            const auto path = std::string("/media/movie-") + bytes + ".mp4";
            const auto original = path;
            const auto encoded = json_string(path);
            require(path == original, "Reporting must never modify the actual filesystem path");
            require(encoded.find("\\ufffd") != std::string::npos && encoded.find(".mp4\"") != std::string::npos, "Invalid bytes must be replaced while preserving the filename suffix");
            for(const auto byte : encoded) require(static_cast<unsigned char>(byte) < 128, "Malformed UTF-8 must not leak into JSON");
        }
        require(json_string(std::string("\xe2") + "A" + "\x82") == "\"\\ufffdA\\ufffd\"", "Invalid sequences must not swallow adjacent ASCII");

        // Emit a document so an external JSON parser can independently verify
        // the encoding; CTest also checks the exact semantic cases above.
        std::cout << "{\"unicode\":" << json_string(unicode) << ",\"controls\":"
                  << json_string(std::string("\0\n\t", 3)) << ",\"invalid_path\":"
                  << json_string(std::string("/media/movie-") + char(0xff) + ".mp4") << "}\n";
        return 0;
    } catch(const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
