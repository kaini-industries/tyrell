import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('chess_device_tools',Path(__file__).with_name('device_acceptance.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
class DeviceInputTest(unittest.TestCase):
    def capabilities(self,fd,request,bits,mutate):
        self.assertEqual(request,0x80604521)
        for code in (28,42,106):bits[code//8]|=1<<(code%8)
    def test_reject_unsupported_key_before_injection(self):
        with patch.object(module.os,'open',return_value=123),patch.object(module.os,'close'),patch.object(module.fcntl,'ioctl',side_effect=self.capabilities),patch.object(module.os,'write') as write:
            keys=module.Keys('/dev/test')
            with self.assertRaisesRegex(RuntimeError,'does not advertise'):keys.send('kpenter',True)
            write.assert_not_called();self.assertFalse(keys.pressed);keys.close()
    def test_partial_chord_failure_releases_supported_key(self):
        with patch.object(module.os,'open',return_value=123),patch.object(module.os,'close'),patch.object(module.fcntl,'ioctl',side_effect=self.capabilities),patch.object(module.os,'write'),patch.object(module.time,'sleep'):
            keys=module.Keys('/dev/test')
            with self.assertRaisesRegex(RuntimeError,'does not advertise'):keys.hold(('shift','kpenter'),.1)
            self.assertFalse(keys.pressed)
            self.assertEqual([(e['key'],e['pressed']) for e in keys.events],[('shift',True),('shift',False)])
            keys.close()
    def test_supported_navigation_press_release(self):
        with patch.object(module.os,'open',return_value=123),patch.object(module.os,'close'),patch.object(module.fcntl,'ioctl',side_effect=self.capabilities),patch.object(module.os,'write') as write,patch.object(module.time,'sleep'):
            keys=module.Keys('/dev/test');keys.tap('right');self.assertFalse(keys.pressed);self.assertEqual(write.call_count,4);keys.close()
if __name__=='__main__':unittest.main()
