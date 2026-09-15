import unittest
from unittest.mock import patch
from srh.profiles.capture import process_environment, canonical_sha256


class CaptureTests(unittest.TestCase):
    def test_environment_excludes_credentials(self):
        raw = b'VLLM_FP8_HYBRID=1\0API_KEY=secret\0VLLM_API_KEY=secret\0HOME=/root\0'
        with patch('srh.profiles.capture.subprocess.check_output', return_value=raw):
            self.assertEqual(process_environment('test'), {'VLLM_FP8_HYBRID': '1'})

    def test_environment_changes_identity(self):
        self.assertNotEqual(canonical_sha256({'process_environment': {'VLLM_FP8_HYBRID': '1'}}),
                            canonical_sha256({'process_environment': {'VLLM_FP8_HYBRID': '0'}}))
