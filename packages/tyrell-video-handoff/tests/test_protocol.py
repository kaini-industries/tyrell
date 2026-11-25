"""Wire parsing is strict and never interprets a packet as device authority."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import handoff_protocol as protocol

S, A, L = "a" * 32, "b" * 32, "c" * 32
T = (S, A, "1", str(protocol.MAX_UINT64))


class ProtocolTests(unittest.TestCase):
    def test_every_declared_verb_has_a_canonical_round_trip(self):
        examples = [protocol.Message("hello", (A, protocol.CAPABILITIES)),
                    protocol.Message("welcome", (S, A, protocol.CAPABILITIES)),
                    protocol.Message("cancel", T)]
        examples += [protocol.Message(verb, (*T, L)) for verb in ("grant", "ready", "released", "commit")]
        examples += [protocol.Message("quiesce", (*T, budget)) for budget in ("1", "10000", "30000")]
        examples += [protocol.Message("quiescing", (*T, phase)) for phase in protocol.PHASES]
        examples += [protocol.Message("failed", (*T, code)) for code in protocol.FAILURE_CODES]
        examples += [protocol.Message("local-done", (*T, checkpoint)) for checkpoint in (L, "none")]
        self.assertEqual({row.verb for row in examples}, set(protocol.FIELD_COUNTS))
        for message in examples:
            with self.subTest(message=message):
                encoded = protocol.encode_packet(message)
                self.assertLessEqual(len(encoded), 512)
                self.assertEqual(protocol.parse_packet(encoded), message)
                self.assertEqual(protocol.encode_packet(protocol.parse_packet(encoded)), encoded)

    def test_unsupported_prefixes_verbs_fields_and_capabilities_fail_closed(self):
        for value in (b"hello\t1", b"TVH1\thello\t" + A.encode(), b"TVH2\tapply\t/sys/brightness\t0",
                      b"TVH2\thello", b"TVH2\tcancel", b"TVH2\tHello\t" + A.encode(),
                      ("TVH2\thello\t" + A + "\tepoch-barrier,async-drain,helper-receipts").encode(),
                      protocol.encode_packet(protocol.Message("cancel", T)) + b"\tignored"):
            with self.subTest(value=value), self.assertRaises(protocol.ProtocolError):
                protocol.parse_packet(value)

    def test_control_characters_nonascii_and_packet_bounds_are_rejected(self):
        good = protocol.encode_packet(protocol.Message("cancel", T))
        for value in (b"", b"x" * 513, b"\xff", b"\xc3\xa9", good + b"\n", good + b"\r",
                      good + b"\x00", good + b"\x7f", b"TVH2\t\thello", bytearray(good), None):
            with self.subTest(value=value), self.assertRaises(protocol.ProtocolError):
                protocol.parse_packet(value)

    def test_epoch_and_request_ids_are_nonzero_canonical_uint64(self):
        for index in (2, 3):
            for bad in ("0", "01", "+1", "-1", "1.0", " 1", "1 ", "",
                        str(protocol.MAX_UINT64 + 1), "9" * 500):
                fields = list(T)
                fields[index] = bad
                with self.subTest(index=index, bad=bad), self.assertRaises(protocol.ProtocolError):
                    protocol.encode_packet(protocol.Message("cancel", tuple(fields)))

    def test_hex_ids_cannot_be_casefolded_or_normalized(self):
        for index in (0, 1):
            for bad in ("a" * 31, "A" * 32, "0x" + "a" * 30, "g" * 32, " " + "a" * 31):
                fields = list(T)
                fields[index] = bad
                with self.subTest(index=index, bad=bad), self.assertRaises(protocol.ProtocolError):
                    protocol.encode_packet(protocol.Message("cancel", tuple(fields)))

    def test_budget_and_fixed_enumerations_are_bounded(self):
        for bad in ("0", "01", "30001", "18446744073709551615", "+1", "forever"):
            with self.subTest(bad=bad), self.assertRaises(protocol.ProtocolError):
                protocol.encode_packet(protocol.Message("quiesce", (*T, bad)))
        for verb, value in (("quiescing", "complete"), ("failed", "custom explanation"),
                            ("local-done", "/tmp/checkpoint"), ("grant", "none")):
            with self.subTest(verb=verb), self.assertRaises(protocol.ProtocolError):
                protocol.encode_packet(protocol.Message(verb, (*T, value)))

    def test_encoding_cannot_smuggle_separators_or_coerce_types(self):
        for value in (None, ("cancel", T), protocol.Message("cancel", list(T)),
                      protocol.Message("cancel", (S, A, 1, "2")),
                      protocol.Message("hello", (A, "x" * 513)),
                      protocol.Message("hello", (A, protocol.CAPABILITIES + "\tignored")),
                      protocol.Message("cancel\t" + S, (A, "1", "2"))):
            with self.subTest(value=value), self.assertRaises(protocol.ProtocolError):
                protocol.encode_packet(value)


if __name__ == "__main__":
    unittest.main()
