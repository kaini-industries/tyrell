#pragma once

#include <algorithm>
#include <initializer_list>
#include <string>
#include <vector>

// Renderer-independent command metadata. Apps retain their own key namespace,
// state, confirmation dialogs and action handlers. No devices or timers live here.
namespace tyrell::app {

enum class Repeat { PressOnly, Allow };
enum class Confirmation { None, Required };

template<class Action> struct Command {
    Action id;
    std::vector<int> keys;
    std::string key_label;
    std::string label;
    std::string help;
    bool enabled = true;
    Repeat repeat = Repeat::PressOnly;
    Confirmation confirmation = Confirmation::None;
    std::string unavailable_reason{};
};

// Disabled matches remain visible to the controller so it can preserve its
// existing unavailable-action feedback. It must check enabled before dispatch.
// Reject ambiguous bindings and repeated confirmations instead of guessing.
template<class Action>
const Command<Action>* find_command(const std::vector<Command<Action>>& commands,
                                    int key, bool repeated = false) {
    const Command<Action>* found = nullptr;
    for(const auto& command : commands) {
        if(std::find(command.keys.begin(), command.keys.end(), key) == command.keys.end()) continue;
        if(found) return nullptr;
        found = &command;
    }
    if(found && repeated && (found->repeat != Repeat::Allow ||
                            found->confirmation == Confirmation::Required)) return nullptr;
    return found;
}

template<class Action>
std::string help_line(const Command<Action>& command) {
    std::string text = command.key_label + "   " + (command.help.empty() ? command.label : command.help);
    if(!command.enabled) text += " (" + (command.unavailable_reason.empty()
                                ? std::string("unavailable") : command.unavailable_reason) + ")";
    return text;
}

template<class Action>
std::vector<std::string> help_lines(const std::vector<Command<Action>>& commands,
                                    bool include_disabled = false) {
    std::vector<std::string> lines;
    for(const auto& command : commands)
        if(command.enabled || include_disabled) lines.push_back(help_line(command));
    return lines;
}

template<class Action>
std::string footer(const std::vector<Command<Action>>& commands,
                   std::initializer_list<Action> actions) {
    std::string text;
    for(const auto action : actions) {
        const auto found = std::find_if(commands.begin(), commands.end(),
                                       [&](const auto& command) { return command.id == action; });
        if(found == commands.end() || !found->enabled) continue;
        if(!text.empty()) text += "   ";
        text += found->key_label + " " + found->label;
    }
    return text;
}

// Check a concrete context, not a union of mutually exclusive screen commands.
template<class Action>
std::vector<std::string> command_errors(const std::vector<Command<Action>>& commands) {
    std::vector<std::string> errors;
    for(std::size_t i = 0; i < commands.size(); ++i) {
        const auto& command = commands[i];
        const auto name = command.key_label.empty() ? std::string("command ") + std::to_string(i) : command.key_label;
        if(command.keys.empty() || command.key_label.empty() || command.label.empty())
            errors.push_back(name + ": missing key binding or label");
        if(command.confirmation == Confirmation::Required && command.repeat != Repeat::PressOnly)
            errors.push_back(name + ": confirmation must be press-only");
        if(!command.enabled && command.unavailable_reason.empty())
            errors.push_back(name + ": disabled command needs a reason");
        for(std::size_t k = 0; k < command.keys.size(); ++k) {
            if(command.keys[k] <= 0 || std::find(command.keys.begin(), command.keys.begin() + k,
                                              command.keys[k]) != command.keys.begin() + k)
                errors.push_back(name + ": invalid or duplicate key alias");
        }
        for(std::size_t j = 0; j < i; ++j) {
            if(command.id == commands[j].id) errors.push_back(name + ": duplicate action");
            for(const auto key : command.keys)
                if(std::find(commands[j].keys.begin(), commands[j].keys.end(), key) != commands[j].keys.end())
                    errors.push_back(name + ": ambiguous key binding");
        }
    }
    return errors;
}

} // namespace tyrell::app
