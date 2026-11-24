#pragma once

#include "tyrell/app/commands.hpp"
#include "lvgl.h"

namespace tyrell::video {
enum class Page { Browser, Player, Menu, Error };
enum class Action {
    Navigate, Parent, Open, OpenDirection, ExitBrowser, Help, Folders, Quit,
    Confirm, Back, Recover, Pause, Seek, Volume, Mute, Output, Tracks, Sound
};
using Command = tyrell::app::Command<Action>;

inline std::vector<Command> commands_for(Page page, bool engine, bool selected,
                                          bool menu_selection, bool hdmi = false) {
    using tyrell::app::Repeat;
    using tyrell::app::Confirmation;
    std::vector<Command> result;
    if(page == Page::Browser) {
        result = {
            {Action::Navigate, {LV_KEY_UP, LV_KEY_DOWN, 'w', 'W', 's', 'S'}, "Up/Down", "Select", "Select file (W/S)", true, Repeat::Allow, Confirmation::None, {}},
            {Action::Parent, {LV_KEY_LEFT, 'a', 'A'}, "Left/A", "Parent", "Parent folder", true, Repeat::Allow, Confirmation::None, {}},
            {Action::Open, {LV_KEY_ENTER}, "Enter", "Play", "Open file or folder", selected, Repeat::PressOnly, Confirmation::None, "no selection"},
            {Action::OpenDirection, {LV_KEY_RIGHT, 'd', 'D'}, "Right/D", "Open", "Open file or folder", selected, Repeat::PressOnly, Confirmation::None, "no selection"},
            {Action::ExitBrowser, {LV_KEY_ESC}, "Esc", "Exit", "Exit to APPLaunch", true, Repeat::PressOnly, Confirmation::None, {}},
        };
    } else if(page == Page::Menu) {
        result = {
            {Action::Navigate, {LV_KEY_UP, LV_KEY_DOWN, 'w', 'W', 's', 'S'}, "Up/Down", "Select", "Select row (W/S)", true, Repeat::Allow, Confirmation::None, {}},
            {Action::Confirm, {LV_KEY_ENTER}, "Enter", "OK", "Confirm selection", menu_selection, Repeat::PressOnly, Confirmation::Required, "no selection"},
            {Action::Back, {LV_KEY_ESC, LV_KEY_LEFT, 'a', 'A'}, "Esc", "Back", "Close menu (Left/A)", true, Repeat::PressOnly, Confirmation::None, {}},
        };
    } else if(page == Page::Error) {
        result = {{Action::Recover, {LV_KEY_ENTER, LV_KEY_ESC}, "Enter/Esc", "Files", "Return to files", true, Repeat::PressOnly, Confirmation::None, {}}};
    } else {
        result = {
            {Action::Pause, {' ', LV_KEY_ENTER}, "Space", "Pause", "Pause/resume (Enter)", engine, Repeat::PressOnly, Confirmation::None, "no player"},
            {Action::Seek, {LV_KEY_LEFT, LV_KEY_RIGHT, 'a', 'A', 'd', 'D'}, "A/D", "Seek 10s", "Seek 10s (Left/Right)", engine, Repeat::Allow, Confirmation::None, "no player"},
            {Action::Volume, {LV_KEY_UP, LV_KEY_DOWN, 'w', 'W', 's', 'S'}, "W/S", "Volume", "Volume (Up/Down)", engine, Repeat::Allow, Confirmation::None, "no player"},
            {Action::Output, {'o', 'O'}, "O", hdmi ? "LCD" : "HDMI", "LCD / HDMI video", engine, Repeat::PressOnly, Confirmation::None, "no player"},
            {Action::Tracks, {'t', 'T'}, "T", "Tracks", "Audio/subtitle tracks", engine, Repeat::PressOnly, Confirmation::None, "no player"},
            {Action::Sound, {'v', 'V'}, "V", "Sound", "Sound output", engine, Repeat::PressOnly, Confirmation::None, "no player"},
            {Action::Mute, {'m', 'M'}, "M", "Mute", "Mute/unmute", engine, Repeat::PressOnly, Confirmation::None, "no player"},
            {Action::Back, {LV_KEY_ESC}, "Esc", "Files", "Stop and return to files", engine, Repeat::PressOnly, Confirmation::None, "no player"},
        };
    }
    if(page == Page::Browser || page == Page::Player) {
        result.push_back({Action::Folders, {'b', 'B'}, "B", "Folders", "Folders / mounted media", true, Repeat::PressOnly, Confirmation::None, {}});
        result.push_back({Action::Help, {'h', 'H'}, "H", "Help", "Contextual key guide", true, Repeat::PressOnly, Confirmation::None, {}});
    }
    result.push_back({Action::Quit, {'q', 'Q'}, "Q", "Exit", "Exit to APPLaunch", true, Repeat::PressOnly, Confirmation::None, {}});
    return result;
}
} // namespace tyrell::video
