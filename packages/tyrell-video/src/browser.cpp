#include "browser.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdlib>
#include <iomanip>
#include <sstream>
#include <system_error>

namespace tyrell::video {
namespace {
std::string lower(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return value;
}
}

Browser::Browser(std::filesystem::path directory) {
    open_directory(directory.empty() ? default_directory() : directory);
}

bool Browser::is_video(const std::filesystem::path& path) {
    static const std::vector<std::string> extensions = {".mp4", ".m4v", ".mkv", ".webm", ".avi", ".mov", ".ogv", ".ogg", ".mpeg", ".mpg", ".ts", ".mts", ".m2ts", ".3gp", ".flv", ".wmv"};
    return std::find(extensions.begin(), extensions.end(), lower(path.extension().string())) != extensions.end();
}

bool Browser::open_directory(const std::filesystem::path& directory) {
    std::error_code ec;
    auto path = std::filesystem::weakly_canonical(directory, ec);
    if(ec || !std::filesystem::is_directory(path, ec)) { error_ = "Cannot open folder: " + directory.string(); return false; }
    std::filesystem::directory_iterator iterator(path, ec);
    if(ec) { error_ = "Cannot read folder: " + path.string(); return false; }
    std::vector<BrowserEntry> next;
    if(path.has_parent_path() && path != path.root_path()) next.push_back({path.parent_path(), "..", true});
    bool truncated = false;
    while(iterator != std::filesystem::directory_iterator()) {
        const auto entry = *iterator;
        std::error_code type_error;
        const bool directory_entry = entry.is_directory(type_error);
        if(!type_error && (directory_entry || (entry.is_regular_file(type_error) && is_video(entry.path())))) {
            next.push_back({entry.path(), entry.path().filename().string(), directory_entry});
            if(next.size() >= 4096) { truncated = true; break; }
        }
        iterator.increment(ec);
        if(ec) break;
    }
    const auto first = !next.empty() && next.front().name == ".." ? next.begin() + 1 : next.begin();
    std::sort(first, next.end(), [](const auto& a, const auto& b) {
        if(a.directory != b.directory) return a.directory;
        const auto left = lower(a.name), right = lower(b.name);
        return left == right ? a.name < b.name : left < right;
    });
    directory_ = std::move(path);
    entries_ = std::move(next);
    selected_ = 0;
    error_ = truncated ? "Showing first 4096 entries; choose a smaller folder" : (ec ? "Some folder entries could not be read" : "");
    return true;
}

const BrowserEntry* Browser::selected() const { return selected_ < entries_.size() ? &entries_[selected_] : nullptr; }
bool Browser::enter_selected() { const auto* entry = selected(); return entry && entry->directory && open_directory(entry->path); }
void Browser::select(std::size_t index) { selected_ = entries_.empty() ? 0 : std::min(index, entries_.size() - 1); }
void Browser::move(int delta) {
    const auto count = static_cast<long long>(entries_.size());
    if(count == 0) return;
    const auto next = static_cast<long long>(selected_) + delta;
    selected_ = static_cast<std::size_t>(std::clamp(next, 0LL, count - 1));
}

std::filesystem::path Browser::home() {
    const char* value = std::getenv("HOME");
    return value && *value ? std::filesystem::path(value) : std::filesystem::path("/");
}
std::filesystem::path Browser::default_directory() {
    std::error_code ec;
    const auto videos = home() / "Videos";
    return std::filesystem::is_directory(videos, ec) ? videos : home();
}
std::vector<std::filesystem::path> Browser::media_roots() {
    std::vector<std::filesystem::path> result = {home()};
    std::error_code ec;
    const auto videos = home() / "Videos";
    if(std::filesystem::is_directory(videos, ec)) result.push_back(videos);
    for(const auto& base : {std::filesystem::path("/media"), std::filesystem::path("/run/media"), std::filesystem::path("/mnt")}) {
        ec.clear();
        if(std::filesystem::is_directory(base, ec)) result.push_back(base);
    }
    return result;
}

std::string format_time(double seconds) {
    if(!std::isfinite(seconds) || seconds < 0) return "--:--";
    const auto total = static_cast<unsigned long long>(std::min(seconds, 359999.0));
    std::ostringstream out;
    if(total >= 3600) out << total / 3600 << ':' << std::setw(2) << std::setfill('0');
    out << (total / 60) % 60 << ':' << std::setw(2) << std::setfill('0') << total % 60;
    return out.str();
}
double seek_target(double position, double delta, double duration) {
    if(!std::isfinite(position) || !std::isfinite(delta)) return 0;
    const auto target = std::max(0.0, position + delta);
    return std::isfinite(duration) && duration >= 0 ? std::min(target, duration) : target;
}
std::string display_text(const std::string& value) {
    // The compact built-in UI font has a Latin glyph set. Keep the real UTF-8
    // path untouched, and render a single replacement for unsupported glyphs.
    std::string result;
    for(std::size_t i = 0; i < value.size();) {
        const auto c = static_cast<unsigned char>(value[i]);
        if(c >= 32 && c < 127) { result += static_cast<char>(c); ++i; }
        else if(c < 128) { result += ' '; ++i; }
        else {
            result += '?'; ++i;
            while(i < value.size() && (static_cast<unsigned char>(value[i]) & 0xc0) == 0x80) ++i;
        }
    }
    return result;
}
} // namespace tyrell::video
