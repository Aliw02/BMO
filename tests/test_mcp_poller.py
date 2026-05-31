"""
Tests for tools/mcp_server.py — async tunnel URL polling.
"""

import os
import sys
import tempfile
import unittest
import asyncio
from unittest.mock import patch

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

import tools.mcp_server as mcp


class TestPollLogForUrlAsync(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.log_file = os.path.join(self.temp_dir, "tunnel.log")

    def tearDown(self):
        if os.path.exists(self.log_file):
            os.remove(self.log_file)
        if os.path.exists(self.temp_dir):
            os.rmdir(self.temp_dir)

    def test_returns_none_when_no_url(self):
        with open(self.log_file, "w") as f:
            f.write("some log content without a url")
        result = asyncio.run(mcp._poll_log_for_url_async(self.log_file, timeout=5))
        self.assertIsNone(result)

    def test_returns_url_when_found(self):
        with open(self.log_file, "w") as f:
            f.write("hello https://abc-123.trycloudflare.com world")
        result = asyncio.run(mcp._poll_log_for_url_async(self.log_file, timeout=5))
        self.assertEqual(result, "https://abc-123.trycloudflare.com")

    def test_timeout_returns_none(self):
        result = asyncio.run(mcp._poll_log_for_url_async("/nonexistent/file.log", timeout=3))
        self.assertIsNone(result)
