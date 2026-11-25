"""Controlled app peer for process/socket tests. No resources or service calls."""
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from handoff_channel import Channel
from handoff_protocol import CAPABILITIES, Message

app = "a" * 32
channel = Channel.adopt(int(sys.argv[1]), expected_uid=os.getuid(), expected_pid=int(sys.argv[2]))
last = None
print("started", flush=True)
for command in sys.stdin:
    command = command.strip()
    if command == "hello":
        result = channel.send(Message("hello", (app, CAPABILITIES)))
    elif command == "receive":
        deadline = time.monotonic() + 2
        last = None
        while last is None and not channel.lost and time.monotonic() < deadline:
            last = channel.receive()
            if last is None:
                time.sleep(0.002)
        result = None if last is None else {"verb": last.verb, "fields": list(last.fields)}
    elif command == "done":
        result = channel.send(Message("local-done", (*last.fields[:4], "none")))
    elif command == "forge-released":
        result = channel.send(Message("released", (*last.fields[:4], "f" * 32)))
    elif command == "before-hello":
        result = channel.send(Message("ready", ("b" * 32, app, "1", "1", "c" * 32)))
    elif command == "exit":
        break
    else:
        raise RuntimeError("unknown fixture command")
    print(json.dumps(result), flush=True)
channel.close()
