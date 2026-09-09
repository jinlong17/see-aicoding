import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from see_aicoding import launcher


class LauncherTests(unittest.TestCase):
    def test_existing_server_is_reused_without_spawning(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(launcher.Path, "home", return_value=Path(tmp)), \
             patch.object(launcher, "server_ready", return_value=True), \
             patch.object(launcher.subprocess, "Popen") as spawn, \
             patch.object(launcher.webbrowser, "open") as open_browser:
            self.assertEqual(launcher.launch(), "http://127.0.0.1:8765/")
            spawn.assert_not_called()
            open_browser.assert_called_once_with("http://127.0.0.1:8765/")

    def test_new_server_is_detached_and_waited_until_ready(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(launcher.Path, "home", return_value=Path(tmp)), \
             patch.object(launcher, "server_ready", side_effect=[False, True]), \
             patch.object(launcher.subprocess, "Popen") as spawn, \
             patch.object(launcher.webbrowser, "open") as open_browser:
            launcher.launch(8766, open_browser=False)
            self.assertTrue(spawn.call_args.kwargs["start_new_session"])
            self.assertIn("8766", spawn.call_args.args[0])
            open_browser.assert_not_called()

    def test_startup_failure_does_not_open_a_broken_page(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(launcher.Path, "home", return_value=Path(tmp)), \
             patch.object(launcher, "server_ready", return_value=False), \
             patch.object(launcher.subprocess, "Popen") as spawn, \
             patch.object(launcher.webbrowser, "open") as open_browser:
            spawn.return_value.poll.return_value = 1
            with self.assertRaisesRegex(RuntimeError, "启动失败"):
                launcher.launch()
            open_browser.assert_not_called()
