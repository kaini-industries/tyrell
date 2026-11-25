"""Strict TVH2 control packets. Parsing confers no authority or device rights."""
from __future__ import annotations

from dataclasses import dataclass
import re

MAX_PACKET_BYTES = 512
MAX_UINT64 = (1 << 64) - 1
CAPABILITIES = "async-drain,epoch-barrier,helper-receipts"
PHASES = frozenset(("local", "graph", "display", "audio", "stop-post"))
FAILURE_CODES = frozenset(("unsupported", "malformed", "stale", "busy", "expired",
                           "identity-lost", "backend-failed", "completion-unknown"))
HEX_ID = re.compile(r"[0-9a-f]{32}\Z")
DECIMAL = re.compile(r"[1-9][0-9]{0,19}\Z")
FIELD_COUNTS = {"hello": 2, "welcome": 3, "grant": 5, "ready": 5, "quiesce": 5,
                "quiescing": 5, "local-done": 5, "released": 5, "commit": 5,
                "cancel": 4, "failed": 5}


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class Message:
    verb: str
    fields: tuple[str, ...]


def _require(value, message):
    if not value:
        raise ProtocolError(message)


def _hex(value):
    _require(HEX_ID.fullmatch(value), "expected a 128-bit lowercase hexadecimal ID")


def _decimal(value, maximum=MAX_UINT64):
    _require(DECIMAL.fullmatch(value) and int(value) <= maximum,
             "expected a canonical nonzero bounded decimal counter")


def parse_packet(packet: bytes) -> Message:
    """Parse one complete packet; roles, epochs and receipts need authority checks."""
    _require(isinstance(packet, bytes) and 0 < len(packet) <= MAX_PACKET_BYTES,
             "packet must contain 1..512 bytes")
    try:
        text = packet.decode("ascii")
    except UnicodeDecodeError as error:
        raise ProtocolError("packet must be ASCII") from error
    _require(all(char == "\t" or 32 <= ord(char) <= 126 for char in text),
             "packet contains control characters")
    words = text.split("\t")
    _require(len(words) >= 2 and words[0] == "TVH2", "unsupported packet prefix")
    verb, fields = words[1], tuple(words[2:])
    _require(verb in FIELD_COUNTS and len(fields) == FIELD_COUNTS[verb],
             "unsupported verb or incorrect field count")
    if verb == "hello":
        _hex(fields[0])
        _require(fields[1] == CAPABILITIES, "required capabilities differ")
    elif verb == "welcome":
        _hex(fields[0]); _hex(fields[1])
        _require(fields[2] == CAPABILITIES, "required capabilities differ")
    else:
        _hex(fields[0]); _hex(fields[1])
        _decimal(fields[2]); _decimal(fields[3])
        if verb in ("grant", "ready", "released", "commit"):
            _hex(fields[4])
        elif verb == "quiesce":
            _decimal(fields[4], 30000)
        elif verb == "quiescing":
            _require(fields[4] in PHASES, "unsupported progress phase")
        elif verb == "local-done":
            if fields[4] != "none":
                _hex(fields[4])
        elif verb == "failed":
            _require(fields[4] in FAILURE_CODES, "unsupported failure code")
    return Message(verb, fields)


def encode_packet(message: Message) -> bytes:
    _require(isinstance(message, Message) and isinstance(message.verb, str) and
             isinstance(message.fields, tuple),
             "expected a Message with text fields in a tuple")
    _require(message.verb in FIELD_COUNTS and len(message.fields) == FIELD_COUNTS[message.verb] and
             all(isinstance(value, str) for value in message.fields), "unsupported verb or fields")
    _require(5 + len(message.verb) + len(message.fields) + sum(map(len, message.fields)) <= MAX_PACKET_BYTES,
             "encoded packet exceeds 512 bytes")
    try:
        packet = "\t".join(("TVH2", message.verb, *message.fields)).encode("ascii")
    except UnicodeEncodeError as error:
        raise ProtocolError("packet must be ASCII") from error
    _require(parse_packet(packet) == message, "field contains a packet separator")
    return packet
