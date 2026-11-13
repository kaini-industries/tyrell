#pragma once

#include <cstddef>
#include <filesystem>
#include <string>
#include <vector>

namespace tyrell::video {
struct BrowserEntry {
    std::filesystem::path path;
    std::string name;
    bool directory = false;
};

class Browser {
public:
    explicit Browser(std::filesystem::path directory = {});
    bool open_directory(const std::filesystem::path& directory);
    bool enter_selected();
    void move(int delta);
    void select(std::size_t index);
    const BrowserEntry* selected() const;
    const std::vector<BrowserEntry>& entries() const { return entries_; }
    const std::filesystem::path& directory() const { return directory_; }
    std::size_t index() const { return selected_; }
    const std::string& error() const { return error_; }
    static bool is_video(const std::filesystem::path& path);
    static std::filesystem::path home();
    static std::filesystem::path default_directory();
    static std::vector<std::filesystem::path> media_roots();
private:
    std::filesystem::path directory_;
    std::vector<BrowserEntry> entries_;
    std::size_t selected_ = 0;
    std::string error_;
};

std::string format_time(double seconds);
double seek_target(double position, double delta, double duration);
std::string display_text(const std::string& value);
} // namespace tyrell::video
