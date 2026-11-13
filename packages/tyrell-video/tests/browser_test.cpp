#include "browser.h"

#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <chrono>

using namespace tyrell::video;
namespace fs = std::filesystem;
void require(bool value, const char* message) { if(!value) throw std::runtime_error(message); }

int main() {
    const auto root = fs::temp_directory_path() / ("tyrell-video-browser-" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
    try {
        fs::create_directories(root / "folder");
        std::ofstream(root / "Zebra.MP4") << "fixture";
        std::ofstream(root / "alpha.mkv") << "fixture";
        std::ofstream(root / "notes.txt") << "hidden";
        std::ofstream(root / "café.webm") << "utf8";
        Browser browser(root);
        require(browser.entries().size() == 5, "Only folders and videos should appear");
        require(browser.entries()[0].name == ".." && browser.entries()[1].directory, "Parent and folders sort first");
        require(browser.entries()[2].name == "alpha.mkv", "Video names sort case insensitively");
        browser.move(-100); require(browser.index() == 0, "Navigation clamps at start");
        browser.move(100); require(browser.index() == 4, "Navigation clamps at end");
        browser.select(1); require(browser.enter_selected(), "Enter opens directory");
        require(browser.directory().filename() == "folder", "Correct directory entered");
        require(browser.enter_selected(), "Parent opens");
        const auto before = browser.directory();
        require(!browser.open_directory(root / "missing"), "Missing directory rejected");
        require(browser.directory() == before && !browser.error().empty(), "Failed navigation preserves usable browser");
        require(Browser::is_video("hello.AVI") && !Browser::is_video("hello.srt"), "Extension filtering");
        require(format_time(0) == "0:00" && format_time(65) == "1:05" && format_time(3665) == "1:01:05", "Time formatting");
        require(format_time(-1) == "--:--", "Unknown time");
        require(seek_target(5, -10, 100) == 0 && seek_target(95, 10, 100) == 100, "Seek clamps");
        require(display_text("café\nfilm") == "caf? film", "Safe display text preserves path separately");
        fs::remove_all(root);
        std::cout << "Browser and playback display model checks passed\n";
        return 0;
    } catch(const std::exception& error) {
        fs::remove_all(root);
        std::cerr << error.what() << '\n'; return 1;
    }
}
