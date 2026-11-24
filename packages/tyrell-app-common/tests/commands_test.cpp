#include "tyrell/app/commands.hpp"
#include <iostream>
#include <stdexcept>

namespace {
enum class Action { Move, Confirm, Hint };
using Command = tyrell::app::Command<Action>;
using tyrell::app::Repeat;
using tyrell::app::Confirmation;
void require(bool value, const char* message) {
    if(!value) throw std::runtime_error(message);
}
}

int main() {
    try {
        std::vector<Command> commands = {
            {Action::Move, {10, 'w'}, "Up/W", "Move", "Move selection", true, Repeat::Allow},
            {Action::Confirm, {13}, "Enter", "Confirm", "Confirm selection", true,
                Repeat::PressOnly, Confirmation::Required},
            {Action::Hint, {'h'}, "H", "Hint", "Show hint", false, Repeat::PressOnly,
                Confirmation::None, "untimed offline games only"},
        };
        require(tyrell::app::command_errors(commands).empty(), "Valid context was rejected");
        require(tyrell::app::find_command(commands, 'w', true)->id == Action::Move, "Navigation alias cannot repeat");
        require(!tyrell::app::find_command(commands, 13, true), "Repeated confirmation was dispatched");
        const auto* disabled = tyrell::app::find_command(commands, 'h');
        require(disabled && !disabled->enabled, "Disabled-key feedback was lost");
        require(tyrell::app::help_lines(commands).size() == 2, "Unavailable action advertised as enabled");
        require(tyrell::app::help_lines(commands, true).back().find("untimed offline") != std::string::npos,
                "Unavailable-action explanation missing");
        require(tyrell::app::footer(commands, {Action::Confirm, Action::Hint}) == "Enter Confirm",
                "Footer disagrees with command availability");
        commands[1].repeat = Repeat::Allow;
        require(!tyrell::app::find_command(commands, 13, true), "Misconfigured confirmation became repeatable");
        require(!tyrell::app::command_errors(commands).empty(), "Invalid confirmation policy was not reported");
        commands[1].keys = {'w'};
        require(!tyrell::app::find_command(commands, 'w'), "Ambiguous key selected an arbitrary handler");
        require(!tyrell::app::command_errors(commands).empty(), "Conflicting commands were not reported");
        std::cout << "Command availability, aliases, help and confirmation policies passed.\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
