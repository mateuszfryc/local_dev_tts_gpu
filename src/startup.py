import logging
import subprocess
import sys

from src.settings import WORKSPACE_ROOT, is_frozen_app


RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "DevSTT"


class WindowsStartup:
    def expected_command(self) -> str:
        if is_frozen_app():
            args = [sys.executable]
        else:
            args = [sys.executable, str(WORKSPACE_ROOT / "main.py")]
        return subprocess.list2cmdline(args)

    def is_enabled(self) -> bool:
        registered_command = self.get_registered_command()
        if registered_command is None:
            return False
        return registered_command == self.expected_command()

    def enable(self) -> None:
        command = self.expected_command()
        logging.info("enabling Windows startup command=%s", command)
        winreg = self._winreg()
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            RUN_KEY_PATH,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(key, RUN_VALUE_NAME, 0, winreg.REG_SZ, command)

    def disable(self) -> None:
        logging.info("disabling Windows startup")
        winreg = self._winreg()
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                RUN_KEY_PATH,
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                winreg.DeleteValue(key, RUN_VALUE_NAME)
        except FileNotFoundError:
            logging.info("Windows startup value already absent")

    def get_registered_command(self) -> str | None:
        winreg = self._winreg()
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                RUN_KEY_PATH,
                0,
                winreg.KEY_QUERY_VALUE,
            ) as key:
                value, value_type = winreg.QueryValueEx(key, RUN_VALUE_NAME)
        except FileNotFoundError:
            return None
        if value_type != winreg.REG_SZ or not isinstance(value, str):
            logging.warning("ignored unsupported Windows startup value type=%s", value_type)
            return None
        return value

    def _winreg(self):
        if sys.platform != "win32":
            raise RuntimeError("Windows startup is only available on Windows.")
        import winreg

        return winreg
