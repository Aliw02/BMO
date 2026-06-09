"""Tests for the profiler engine."""
import asyncio
import os
import tempfile
import unittest
from unittest.mock import AsyncMock

from core.profiler_engine import ProfilerEngine


class TestProfilerEngine(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
        self.tmp.write("# User Profile\n\n## Recent Observations\n\n- [2024-01-01] Test observation\n\n---\n")
        self.tmp.close()
        self.engine = ProfilerEngine(self.tmp.name)

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_should_run_first_time(self):
        self.assertTrue(self.engine.should_run(1, 7))

    def test_should_run_not_yet(self):
        self.assertFalse(self.engine.should_run(1, 3))

    def test_should_run_after_threshold(self):
        self.engine.should_run(1, 7)
        self.assertTrue(self.engine.should_run(1, 14))

    def test_should_run_skip_before_threshold(self):
        self.engine.should_run(1, 7)
        self.assertFalse(self.engine.should_run(1, 10))

    def test_get_profile_content(self):
        content = self.engine.get_profile_content()
        self.assertIn("# User Profile", content)
        self.assertIn("Test observation", content)

    def test_get_profile_content_missing_file(self):
        engine = ProfilerEngine("/nonexistent/path.md")
        content = engine.get_profile_content()
        self.assertEqual(content, "")

    def test_write_observation(self):
        result = self.engine.write_observation("PREFERENCE: User likes short answers")
        self.assertTrue(result)
        content = self.engine.get_profile_content()
        self.assertIn("likes short answers", content)

    def test_write_observation_empty(self):
        result = self.engine.write_observation("")
        self.assertFalse(result)

    def test_extract_stable_observations(self):
        text = "PREFERENCE: short answers\nHABIT: asks at night\nCONSTRAINT: no auto-deploy\nTRAIT: decisive"
        result = self.engine.extract_stable_observations(text)
        self.assertIn("short answers", result["preferences"])
        self.assertIn("asks at night", result["habits"])
        self.assertIn("no auto-deploy", result["constraints"])
        self.assertIn("decisive", result["traits"])

    def test_write_observation_updates_timestamp(self):
        self.engine.write_observation("TRAIT: patient")
        content = self.engine.get_profile_content()
        self.assertRegex(content, r"Last updated: \d{4}-\d{2}-\d{2}")

    def test_analyze_and_update_returns_observation(self):
        mock_client = AsyncMock()
        mock_client.send_simple_query = AsyncMock(return_value="PREFERENCE: short answers")
        result = asyncio.run(self.engine.analyze_and_update(
            chat_id=1,
            user_message="be brief",
            assistant_response="ok",
            session_context="existing",
            opencode_client=mock_client,
        ))
        self.assertEqual(result, "PREFERENCE: short answers")

    def test_analyze_and_update_returns_none_on_no_observation(self):
        mock_client = AsyncMock()
        mock_client.send_simple_query = AsyncMock(return_value="NO_OBSERVATION")
        result = asyncio.run(self.engine.analyze_and_update(
            chat_id=1, user_message="hi", assistant_response="hello",
            session_context="", opencode_client=mock_client,
        ))
        self.assertIsNone(result)

    def test_analyze_and_update_handles_error(self):
        mock_client = AsyncMock()
        mock_client.send_simple_query = AsyncMock(side_effect=Exception("fail"))
        result = asyncio.run(self.engine.analyze_and_update(
            chat_id=1, user_message="hi", assistant_response="hello",
            session_context="", opencode_client=mock_client,
        ))
        self.assertIsNone(result)
