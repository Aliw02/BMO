"""
Tests for tools/task_registry.py — Background Task Registry.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

import tools.task_registry as tr


class TestTaskRegistryBase(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_registry = os.path.join(self.temp_dir, "background_tasks.json")
        self.original_path = tr.REGISTRY_PATH
        tr.REGISTRY_PATH = self.temp_registry
        self._patcher = patch("tools.task_registry._is_process_alive", return_value=True)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        tr.REGISTRY_PATH = self.original_path
        if os.path.exists(self.temp_registry):
            os.remove(self.temp_registry)
        if os.path.exists(self.temp_dir):
            os.rmdir(self.temp_dir)


class TestEnsureRegistry(TestTaskRegistryBase):

    def test_creates_registry_if_missing(self):
        self.assertFalse(os.path.exists(self.temp_registry))
        tr._ensure_registry()
        self.assertTrue(os.path.exists(self.temp_registry))
        with open(self.temp_registry) as f:
            data = json.load(f)
        self.assertEqual(data, {"tasks": []})

    def test_does_not_overwrite_existing(self):
        tr._ensure_registry()
        with open(self.temp_registry, "w") as f:
            json.dump({"tasks": [{"pid": 999}]}, f)
        tr._ensure_registry()
        with open(self.temp_registry) as f:
            data = json.load(f)
        self.assertEqual(len(data["tasks"]), 1)


class TestProcessAlive(unittest.TestCase):
    """Test process liveness detection — no base class (needs real _is_process_alive)."""

    def test_running_process(self):
        with patch("tools.task_registry.psutil.Process") as mock_process:
            mock_proc = MagicMock()
            mock_proc.is_running.return_value = True
            mock_proc.status.return_value = "running"
            mock_process.return_value = mock_proc
            self.assertTrue(tr._is_process_alive(12345))

    def test_dead_process(self):
        with patch("tools.task_registry.psutil.Process") as mock_process:
            mock_process.side_effect = tr.psutil.NoSuchProcess(12345)
            self.assertFalse(tr._is_process_alive(12345))

    def test_zombie_process(self):
        with patch("tools.task_registry.psutil.Process") as mock_process:
            mock_proc = MagicMock()
            mock_proc.is_running.return_value = True
            mock_proc.status.return_value = tr.psutil.STATUS_ZOMBIE
            mock_process.return_value = mock_proc
            self.assertFalse(tr._is_process_alive(12345))


class TestCleanupDeadTasks(TestTaskRegistryBase):

    def test_marks_dead_tasks(self):
        with patch("tools.task_registry._is_process_alive", return_value=False):
            tasks = [
                {"pid": 1, "status": "running"},
                {"pid": 2, "status": "running"},
            ]
            cleaned = tr._cleanup_dead_tasks(tasks)
            self.assertEqual(cleaned[0]["status"], "stopped")
            self.assertIn("stopped_at", cleaned[0])
            self.assertIn("stop_reason", cleaned[0])

    def test_keeps_alive_tasks(self):
        with patch("tools.task_registry._is_process_alive", return_value=True):
            tasks = [{"pid": 1, "status": "running"}]
            cleaned = tr._cleanup_dead_tasks(tasks)
            self.assertEqual(cleaned[0]["status"], "running")

    def test_ignores_already_stopped(self):
        tasks = [{"pid": 1, "status": "stopped"}]
        cleaned = tr._cleanup_dead_tasks(tasks)
        self.assertEqual(cleaned[0]["status"], "stopped")


class TestRegistryCRUD(TestTaskRegistryBase):

    def test_read_empty_registry(self):
        data = tr.read_registry()
        self.assertEqual(data["tasks"], [])

    def test_register_task(self):
        task = tr.register_task(
            pid=12345,
            command="node server.js",
            port=3456,
            task_type="server",
            description="Test server",
            log_file="logs/test.log",
        )
        self.assertEqual(task["pid"], 12345)
        self.assertEqual(task["port"], 3456)
        self.assertEqual(task["status"], "running")

        data = tr.read_registry()
        self.assertEqual(len(data["tasks"]), 1)
        self.assertEqual(data["tasks"][0]["pid"], 12345)

    def test_update_task(self):
        tr.register_task(pid=12345, command="test", port=3456, task_type="server")
        result = tr.update_task(12345, url="https://abc.trycloudflare.com")
        self.assertTrue(result)

        task = tr.get_task(12345)
        self.assertEqual(task["url"], "https://abc.trycloudflare.com")

    def test_update_nonexistent_task(self):
        result = tr.update_task(99999, url="https://example.com")
        self.assertFalse(result)

    def test_remove_task(self):
        tr.register_task(pid=12345, command="test", port=3456, task_type="server")
        result = tr.remove_task(12345)
        self.assertTrue(result)

        data = tr.read_registry()
        self.assertEqual(len(data["tasks"]), 0)

    def test_remove_nonexistent_task(self):
        result = tr.remove_task(99999)
        self.assertFalse(result)


class TestTaskQueries(TestTaskRegistryBase):

    def setUp(self):
        super().setUp()
        tr.register_task(pid=1, command="cmd1", port=3456, task_type="tunnel", description="Tunnel 1")
        tr.register_task(pid=2, command="cmd2", port=8080, task_type="server", description="Server 1")
        tr.register_task(pid=3, command="cmd3", port=3456, task_type="tunnel", description="Tunnel 2")

    def test_get_task(self):
        task = tr.get_task(1)
        self.assertEqual(task["pid"], 1)
        self.assertEqual(task["port"], 3456)

    def test_get_task_not_found(self):
        self.assertIsNone(tr.get_task(999))

    def test_get_tasks_by_port(self):
        tasks = tr.get_tasks_by_port(3456)
        self.assertEqual(len(tasks), 2)

    def test_get_used_ports(self):
        ports = tr.get_used_ports()
        self.assertIn(3456, ports)
        self.assertIn(8080, ports)

    def test_check_port_conflict(self):
        conflict = tr.check_port_conflict(3456)
        self.assertIsNotNone(conflict)
        self.assertEqual(conflict["pid"], 1)

    def test_check_port_no_conflict(self):
        conflict = tr.check_port_conflict(9999)
        self.assertIsNone(conflict)


class TestFormatTaskList(TestTaskRegistryBase):

    def test_empty_list(self):
        result = tr.format_task_list([])
        self.assertEqual(result, "No active background tasks.")

    def test_single_task(self):
        tasks = [
            {
                "pid": 12345,
                "type": "tunnel",
                "port": 3456,
                "status": "running",
                "url": "https://abc.trycloudflare.com",
                "description": "Webchat tunnel",
            }
        ]
        result = tr.format_task_list(tasks)
        self.assertIn("12345", result)
        self.assertIn("3456", result)
        self.assertIn("https://abc.trycloudflare.com", result)
        self.assertIn("Webchat tunnel", result)

    def test_task_without_url(self):
        tasks = [
            {
                "pid": 12345,
                "type": "server",
                "port": 8080,
                "status": "running",
                "url": "",
                "description": "",
            }
        ]
        result = tr.format_task_list(tasks)
        self.assertIn("12345", result)
        self.assertNotIn("→", result)


if __name__ == "__main__":
    unittest.main()
