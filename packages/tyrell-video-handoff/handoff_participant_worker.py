"""Fixed isolated-interpreter entry for explicitly provided audio bootstrap.

Not installed or environment activated. The coordinator supplies three inherited
FDs and a fixed null-audio configuration; there is no arbitrary command loader.
"""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from handoff_authority import identifier, require
from handoff_participants import AudioWorkerConfig, _worker


def main(arguments):
    require(len(arguments) == 6, "fixed participant bootstrap required")
    descriptors = tuple(int(value) for value in arguments[:3])
    require(all(str(number) == text and number > 2 for number,text in zip(descriptors,arguments[:3])) and
            len(set(descriptors)) == 3, "invalid inherited participant descriptors")
    parent_pid = int(arguments[3])
    require(str(parent_pid) == arguments[3] and parent_pid > 0, "invalid registered coordinator")
    binding = tuple(json.loads(arguments[4]))
    require(len(binding) == 4 and all(identifier(value) for value in binding), "invalid participant binding")
    configuration = json.loads(arguments[5])
    require(type(configuration) is dict and set(configuration) == {"server","expected_uid","expected_pid"},
            "invalid fixed audio configuration")
    config = AudioWorkerConfig(**configuration)
    _worker(*descriptors, (), config.factory, binding, "audio", parent_pid, config.expected_uid)


if __name__ == "__main__":
    main(sys.argv[1:])
