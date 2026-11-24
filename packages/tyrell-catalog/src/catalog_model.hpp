#pragma once

#include <cstdint>
#include <string>
#include <optional>
#include <vector>
#include <tyrell/app/commands.hpp>

namespace tyrell::catalog {
struct Artwork {std::string sha256,path;unsigned bytes=0,width=0,height=0;};
struct Package {
    std::string id, title, version, state, summary;
    std::uint64_t bytes=0;
    std::optional<Artwork> artwork;
};
struct Snapshot {
    std::string state="empty", sequence="0", message="No verified catalog. Press R to refresh.";
    std::vector<Package> packages;
    std::optional<std::uint64_t> issued_at,expires_at;
};
Snapshot parse_view(const std::string& data);
std::string parse_result(const std::string& data, bool success);
std::string size_text(std::uint64_t bytes);
enum class Page { List, Details, Help };
enum class Action { Up, Down, Details, Refresh, Download, Artwork, Help, Cancel, Exit };
enum class Request { None, Refresh, Download, Artwork, Cancel, Exit };
constexpr int key_up=1001, key_down=1002, key_enter=1003, key_escape=1004;
class Model {
public:
    Snapshot snapshot;
    std::size_t selected=0;
    Page page=Page::List;
    bool busy=false;
    std::string notice;
    void replace(Snapshot next);
    void invalidate_view(const std::string& message);
    bool revalidate_time(std::int64_t wall_epoch);
    const Package* current() const;
    bool can_download() const;
    std::vector<tyrell::app::Command<Action>> commands() const;
    Request key(int code);
    std::string state_label() const;
};
} // namespace tyrell::catalog
