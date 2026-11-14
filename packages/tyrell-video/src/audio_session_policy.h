#pragma once

#include <cctype>
#include <string>
#include <vector>

namespace tyrell::video {

inline bool valid_audio_lease_token(const std::string& token, unsigned uid) {
    const std::string prefix = "tyrell_video_hdmi_" + std::to_string(uid) + "_";
    if(token.rfind(prefix, 0) != 0 || token.size() > 120) return false;
    const auto split = token.find('_', prefix.size());
    if(split == std::string::npos || split == prefix.size() || split + 1 == token.size()) return false;
    for(std::size_t i = prefix.size(); i < token.size(); ++i)
        if(i != split && !std::isdigit(static_cast<unsigned char>(token[i]))) return false;
    return token[prefix.size()] != '0';
}

// Parse only argument boundaries/quotes; never execute shell text. A token
// embedded in a sink description cannot authorize unloading a foreign module.
inline bool owned_audio_module(const std::string& name, const std::string& arguments,
                               const std::string& token) {
    if(name != "module-alsa-sink") return false;
    std::vector<std::string> words;
    std::string word;
    char quote = 0;
    bool escaped = false;
    for(char c : arguments) {
        if(escaped) { word += c; escaped = false; }
        else if(c == '\\') escaped = true;
        else if(quote) { if(c == quote) quote = 0; else word += c; }
        else if(c == '\'' || c == '"') quote = c;
        else if(std::isspace(static_cast<unsigned char>(c))) {
            if(!word.empty()) { words.push_back(word); word.clear(); }
        } else word += c;
    }
    if(escaped || quote) return false;
    if(!word.empty()) words.push_back(word);
    bool found = false;
    for(const auto& item : words) {
        if(item.rfind("sink_name=", 0) == 0) {
            if(found || item != "sink_name=" + token) return false;
            found = true;
        }
    }
    return found;
}

} // namespace tyrell::video
