import ctypes
import logging
import sys
import time
from ctypes import wintypes

import keyboard
import pyperclip


SW_RESTORE = 9
TEXT_UIA_CONTROL_TYPES = {
    50004,  # Edit
    50026,  # Document
    50030,  # Text
}
TEXT_CLASS_NAMES = {
    "edit",
    "richedit20w",
    "richedit50w",
    "richeditd2dpt",
    "prosemirror",
    "prosemirror-focused",
    "monaco-editor",
    "cm-content",
    "ace_text-input",
}


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


class TranscriptDelivery:
    def __init__(self) -> None:
        self.target_window_hwnd: int | None = None

    def capture_target_window(self) -> int | None:
        self.target_window_hwnd = self.get_foreground_window()
        logging.info("captured target_window_hwnd=%s", self.target_window_hwnd)
        return self.target_window_hwnd

    def deliver_text(self, text: str) -> bool:
        logging.info("deliver_text start text_length=%s", len(text))
        pyperclip.copy(text)
        self.restore_target_window()
        if not self.focused_control_accepts_text():
            logging.info("focused control does not accept text; leaving transcript in clipboard")
            return False

        time.sleep(0.1)
        keyboard.press_and_release("ctrl+v")
        logging.info("ctrl+v sent")
        return True

    def get_foreground_window(self) -> int | None:
        if sys.platform != "win32":
            return None
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        return int(hwnd) if hwnd else None

    def restore_target_window(self) -> None:
        if sys.platform != "win32" or not self.target_window_hwnd:
            logging.info("restore_target_window skipped target_window_hwnd=%s", self.target_window_hwnd)
            return
        try:
            user32 = ctypes.windll.user32
            hwnd = wintypes.HWND(self.target_window_hwnd)
            if not user32.IsWindow(hwnd):
                logging.info("target window no longer exists hwnd=%s", self.target_window_hwnd)
                return

            foreground_hwnd = user32.GetForegroundWindow()
            if foreground_hwnd == self.target_window_hwnd:
                logging.info("target window already foreground hwnd=%s", self.target_window_hwnd)
                return

            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, SW_RESTORE)
                logging.info("target window was minimized; restored hwnd=%s", self.target_window_hwnd)

            if not user32.SetForegroundWindow(hwnd):
                logging.warning("SetForegroundWindow failed hwnd=%s error=%s", self.target_window_hwnd, ctypes.WinError())
            else:
                logging.info("target window foreground requested hwnd=%s", self.target_window_hwnd)
            time.sleep(0.2)
        except Exception:
            logging.exception("restore_target_window failed hwnd=%s", self.target_window_hwnd)

    def focused_control_accepts_text(self) -> bool:
        logging.info("checking focused control")
        if self.uia_focused_control_accepts_text():
            return True
        return self.win32_focused_control_accepts_text()

    def uia_focused_control_accepts_text(self) -> bool:
        logging.info("checking focused control via UI Automation")
        try:
            import comtypes.client

            comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen.UIAutomationClient import IUIAutomation

            automation = comtypes.client.CreateObject(
                "{FF48DBA4-60EF-4201-AA87-54103EEF594E}",
                interface=IUIAutomation,
            )
            element = automation.GetFocusedElement()
            if element is None:
                logging.info("UIA focused element is None")
                return False

            control_type = element.CurrentControlType
            class_name = (element.CurrentClassName or "").lower()
            name = element.CurrentName or ""
            logging.info(
                "UIA focused element control_type=%s class=%s name=%s",
                control_type,
                class_name,
                name,
            )
            if control_type in TEXT_UIA_CONTROL_TYPES:
                return True
            class_tokens = set(class_name.replace(".", " ").replace("-", " ").split())
            return class_name in TEXT_CLASS_NAMES or bool(class_tokens & TEXT_CLASS_NAMES)
        except Exception:
            logging.exception("failed to inspect focused control via UI Automation")
            return False

    def win32_focused_control_accepts_text(self) -> bool:
        logging.info("checking focused control via Win32")
        if sys.platform != "win32":
            return False
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                logging.info("no foreground window")
                return False

            thread_id = user32.GetWindowThreadProcessId(hwnd, None)
            gui_info = GUITHREADINFO()
            gui_info.cbSize = ctypes.sizeof(GUITHREADINFO)
            if not user32.GetGUIThreadInfo(thread_id, ctypes.byref(gui_info)):
                logging.warning("GetGUIThreadInfo failed: %s", ctypes.WinError())
                return False

            focus_hwnd = gui_info.hwndFocus
            if not focus_hwnd:
                logging.info("no focused hwnd")
                return False

            class_name_buffer = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(focus_hwnd, class_name_buffer, len(class_name_buffer))
            class_name = class_name_buffer.value.lower()
            logging.info(
                "Win32 focused hwnd=%s class=%s foreground=%s",
                focus_hwnd,
                class_name,
                hwnd,
            )
            return class_name in TEXT_CLASS_NAMES
        except Exception:
            logging.exception("failed to inspect focused control via Win32")
            return False

