"""Retry-After handling without real network requests or provider credentials."""
from datetime import datetime, timedelta, timezone
from email.message import Message
from email.utils import format_datetime
from io import BytesIO
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from collector.network import RequestError, parse_retry_after, request


class RetryAfterTests(unittest.TestCase):
    def test_seconds_and_http_dates_round_up_and_ignore_invalid_values(self):
        now = datetime(2026, 11, 11, 1, 30, 0, 500000, tzinfo=timezone.utc)
        self.assertEqual(parse_retry_after(' 120 ', now=now), 120)
        self.assertEqual(parse_retry_after('0', now=now), 0)
        self.assertEqual(parse_retry_after(format_datetime(now + timedelta(hours=1)), now=now), 3600)
        self.assertEqual(parse_retry_after(format_datetime(now - timedelta(hours=1)), now=now), 0)
        for value in (None, '', 'secret-looking-text', '-1', 'NaN', 'Infinity', '9' * 100):
            with self.subTest(value=value):
                self.assertIsNone(parse_retry_after(value, now=now))

    @staticmethod
    def error(status=429, retry_after='3600'):
        headers = Message()
        headers['Retry-After'] = retry_after
        headers['X-Private-Header'] = 'private-credential'
        return HTTPError('https://example.com/api', status, 'private-error-message', headers,
                         BytesIO(b'private-provider-response'))

    @patch('collector.network.time.sleep')
    @patch('collector.network.urllib.request.build_opener')
    def test_post_preserves_only_retry_duration_without_resending(self, build, sleep):
        build.return_value.open.side_effect = self.error()
        with self.assertRaises(RequestError) as caught:
            request('https://example.com/api', {'example.com'}, payload=b'private-prompt')
        self.assertEqual(str(caught.exception), 'http_429')
        self.assertEqual(vars(caught.exception), {'reason':'http_429', 'retry_after_seconds':3600})
        self.assertEqual(build.return_value.open.call_count, 1)
        sleep.assert_not_called()

    @patch('collector.network.time.sleep')
    @patch('collector.network.urllib.request.build_opener')
    def test_get_honors_short_wait_and_leaves_long_wait_for_next_collection(self, build, sleep):
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = {}
        response.read.return_value = b'feed'
        build.return_value.open.side_effect = [self.error(retry_after='12'), response]
        self.assertEqual(request('https://example.com/api', {'example.com'}), b'feed')
        sleep.assert_called_once_with(12)
        sleep.reset_mock()
        build.return_value.open.reset_mock()
        build.return_value.open.side_effect = self.error(retry_after='7200')
        with self.assertRaises(RequestError) as caught:
            request('https://example.com/api', {'example.com'})
        self.assertEqual(caught.exception.retry_after_seconds, 7200)
        self.assertEqual(build.return_value.open.call_count, 1)
        sleep.assert_not_called()
