import errno
import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

from see_aicoding import web


class WebPortConflictTests(unittest.TestCase):
    def test_listener_lookup_falls_back_to_lsof_when_psutil_is_denied(self):
        lsof_result = mock.Mock(returncode=0, stdout="p49628\n")

        with (
            mock.patch.object(
                web.psutil,
                "net_connections",
                side_effect=web.psutil.AccessDenied(pid=1),
            ),
            mock.patch.object(web, "subprocess") as subprocess_module,
            mock.patch.object(web.psutil, "Process") as process,
        ):
            subprocess_module.run.return_value = lsof_result
            process.return_value.cmdline.return_value = [
                "/usr/bin/Python",
                "/Users/lijinlong/.local/share/see-aicoding/venv/bin/see-aicoding",
                "--web",
                "--port",
                "8765",
            ]

            command = web._find_loopback_listener_command("127.0.0.1", 8765)

        self.assertIn("see-aicoding", command)
        self.assertIn("--web", command)
        subprocess_module.run.assert_called_once()
        process.assert_called_once_with(49628)

    def test_existing_see_aicoding_server_opens_existing_url(self):
        bind_error = OSError(errno.EADDRINUSE, "Address already in use")

        with (
            mock.patch.object(web, "MonitorState", return_value=object()),
            mock.patch.object(web, "WebMonitorServer", side_effect=bind_error),
            mock.patch.object(
                web,
                "_find_loopback_listener_command",
                return_value="see-aicoding --web --open",
            ),
            mock.patch.object(web.webbrowser, "open") as open_browser,
            redirect_stdout(io.StringIO()) as stdout,
        ):
            result = web.run_web_server("127.0.0.1", 8765, 1.5, True)

        self.assertEqual(result, 0)
        self.assertIn("see-aicoding web monitor is already running", stdout.getvalue())
        open_browser.assert_called_once_with("http://127.0.0.1:8765/")

    def test_other_listener_keeps_port_in_use_error(self):
        bind_error = OSError(errno.EADDRINUSE, "Address already in use")

        with (
            mock.patch.object(web, "MonitorState", return_value=object()),
            mock.patch.object(web, "WebMonitorServer", side_effect=bind_error),
            mock.patch.object(
                web,
                "_find_loopback_listener_command",
                return_value="python -m http.server 8765",
            ),
            mock.patch.object(web.webbrowser, "open") as open_browser,
        ):
            with self.assertRaises(OSError) as raised:
                web.run_web_server("127.0.0.1", 8765, 1.5, True)

        self.assertIs(raised.exception, bind_error)
        open_browser.assert_not_called()


if __name__ == "__main__":
    unittest.main()
