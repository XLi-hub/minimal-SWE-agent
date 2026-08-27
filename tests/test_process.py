from unittest.mock import MagicMock, patch

from mini_agent.environments.process import terminate_process_group


def test_windows_timeout_terminates_process_tree():
    process = MagicMock(pid=1234)

    with patch("mini_agent.environments.process.os.name", "nt"), \
         patch("mini_agent.environments.process.subprocess.run") as run:
        terminate_process_group(process)

    run.assert_called_once_with(
        ["taskkill", "/PID", "1234", "/T", "/F"],
        capture_output=True,
        check=False,
        timeout=10,
    )
    process.kill.assert_not_called()
