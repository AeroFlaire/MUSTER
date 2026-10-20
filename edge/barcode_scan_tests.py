import unittest
from unittest.mock import patch, MagicMock
import urllib.error
import json
from barcode_scan import process_barcode_and_send

class TestBarcodeScannerAPI(unittest.TestCase):

    def test_empty_string_rejection(self):
        # Should fail locally before ever hitting the API
        success, msg = process_barcode_and_send("")
        self.assertFalse(success)
        self.assertEqual(msg, "Error: No string detected.")

    @patch('urllib.request.urlopen')
    def test_successful_api_transmission(self, mock_urlopen):
        # Configure the mock to simulate Alena's 201 Created response
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"event_id": 11, "status": "ENTERED"}).encode('utf-8')
        mock_urlopen.return_value.__enter__.return_value = mock_response

        success, msg = process_barcode_and_send("PDF417_TEST_DATA_9000000001_END")
        
        self.assertTrue(success)
        self.assertIn("Success: Event 11 opened", msg)
        mock_urlopen.assert_called_once()

    @patch('urllib.request.urlopen')
    def test_api_network_failure(self, mock_urlopen):
        # Configure the mock to simulate the server being offline
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        success, msg = process_barcode_and_send("PDF417_TEST_DATA_9000000001_END")
        
        self.assertFalse(success)
        self.assertIn("Network Error: Is the Gatekeeper API running", msg)

    @patch('urllib.request.urlopen')
    def test_api_http_error(self, mock_urlopen):
        # Configure the mock to simulate a 409 Invalid Transition from the API
        mock_error = urllib.error.HTTPError(
            url="http://127.0.0.1:5000", code=409, msg="Conflict", hdrs={}, 
            fp=MagicMock()
        )
        mock_error.read.return_value = b'{"error": {"code": "invalid_transition"}}'
        mock_urlopen.side_effect = mock_error

        success, msg = process_barcode_and_send("PDF417_TEST_DATA_9000000001_END")
        
        self.assertFalse(success)
        self.assertIn("API Error 409", msg)

if __name__ == '__main__':
    print("Running MUSTER Python Scanner Tests (with Mocked API)...\n" + "-"*50)
    unittest.main(verbosity=2)