import ctypes
import logging
import sys
import threading
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable

from src.settings import DEFAULT_HOTKEY


HOTKEY_ID = 1
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_MENU = 0x12
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_SPACE = 0x20
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

MODIFIER_ORDER = ("ctrl", "shift", "alt", "win")
MODIFIER_FLAGS = {
    "alt": MOD_ALT,
    "ctrl": MOD_CONTROL,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
}
MODIFIER_ALIASES = {
    "control": "ctrl",
    "ctrl": "ctrl",
    "shift": "shift",
    "alt": "alt",
    "menu": "alt",
    "win": "win",
    "windows": "win",
}
SPECIAL_KEY_VKS = {
    "space": VK_SPACE,
    "tab": 0x09,
    "esc": 0x1B,
    "escape": 0x1B,
    "backspace": 0x08,
    "delete": 0x2E,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "prior": 0x21,
    "pagedown": 0x22,
    "next": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
}
IGNORED_SHORTCUT_KEYSYMS = {
    "control_l",
    "control_r",
    "shift_l",
    "shift_r",
    "alt_l",
    "alt_r",
    "super_l",
    "super_r",
    "win_l",
    "win_r",
    "return",
    "enter",
}


@dataclass(frozen=True)
class ParsedHotkey:
    modifiers: int
    vk: int
    text: str


def canonical_hotkey_text(modifiers: set[str], key: str) -> str:
    ordered_modifiers = [modifier for modifier in MODIFIER_ORDER if modifier in modifiers]
    return "+".join((*ordered_modifiers, key))


def key_to_vk(key: str) -> int | None:
    key = key.lower()
    if len(key) == 1 and "a" <= key <= "z":
        return ord(key.upper())
    if len(key) == 1 and "0" <= key <= "9":
        return ord(key)
    if key.startswith("f") and key[1:].isdigit():
        function_key = int(key[1:])
        if 1 <= function_key <= 24:
            return 0x70 + function_key - 1
    return SPECIAL_KEY_VKS.get(key)


def parse_hotkey(hotkey: str) -> ParsedHotkey | None:
    tokens = [token.strip().lower() for token in hotkey.split("+") if token.strip()]
    if not tokens:
        return None

    modifiers: set[str] = set()
    key = ""
    for token in tokens:
        if token in MODIFIER_ALIASES:
            modifiers.add(MODIFIER_ALIASES[token])
            continue
        if key:
            return None
        key = token

    if not modifiers or not key:
        return None

    vk = key_to_vk(key)
    if vk is None:
        return None

    modifier_flags = 0
    for modifier in modifiers:
        modifier_flags |= MODIFIER_FLAGS[modifier]
    return ParsedHotkey(modifier_flags, vk, canonical_hotkey_text(modifiers, key))


def get_pressed_modifier_names() -> set[str]:
    if sys.platform != "win32":
        return set()
    user32 = ctypes.windll.user32

    def pressed(vk: int) -> bool:
        return bool(user32.GetAsyncKeyState(vk) & 0x8000)

    modifiers = set()
    if pressed(VK_CONTROL):
        modifiers.add("ctrl")
    if pressed(VK_SHIFT):
        modifiers.add("shift")
    if pressed(VK_MENU):
        modifiers.add("alt")
    if pressed(VK_LWIN) or pressed(VK_RWIN):
        modifiers.add("win")
    return modifiers


def key_name_from_tk_event(event) -> str | None:
    keysym = (event.keysym or "").lower()
    if keysym in IGNORED_SHORTCUT_KEYSYMS:
        return None
    if len(keysym) == 1 and keysym.isalnum():
        return keysym.lower()
    if keysym in SPECIAL_KEY_VKS:
        return "space" if keysym == "space" else keysym
    if keysym.startswith("f") and keysym[1:].isdigit():
        return keysym
    char = (event.char or "").lower()
    if len(char) == 1 and char.isalnum():
        return char
    return None


class HotkeyManager:
    def __init__(self, on_hotkey: Callable[[], None], stop_event: threading.Event) -> None:
        self.on_hotkey = on_hotkey
        self.stop_event = stop_event
        self.registered = False
        self.thread: threading.Thread | None = None
        self.thread_id: int | None = None
        self.ready = threading.Event()
        self.registration_error: Exception | None = None
        self.registered_hotkey = ""

    def register(self, hotkey_text: str) -> str:
        logging.info("register_hotkey requested already_registered=%s", self.registered)
        if self.registered:
            return self.registered_hotkey

        parsed_hotkey = parse_hotkey(hotkey_text)
        if parsed_hotkey is None:
            logging.warning("configured hotkey invalid; falling back to %s", DEFAULT_HOTKEY)
            parsed_hotkey = parse_hotkey(DEFAULT_HOTKEY)
        if parsed_hotkey is None:
            logging.error("default hotkey is invalid: %s", DEFAULT_HOTKEY)
            return ""

        self.ready.clear()
        self.registration_error = None
        self.thread = threading.Thread(
            target=self._message_loop,
            args=(parsed_hotkey,),
            name="win32-hotkey",
            daemon=True,
        )
        self.thread.start()
        if not self.ready.wait(timeout=2.0):
            logging.error("hotkey registration timed out: %s", parsed_hotkey.text)
            return ""
        if self.registration_error is not None:
            logging.error(
                "hotkey registration failed: %s error=%s",
                parsed_hotkey.text,
                self.registration_error,
            )
            return ""
        self.registered_hotkey = parsed_hotkey.text
        logging.info(
            "hotkey registered: %s modifiers=%s vk=%s",
            parsed_hotkey.text,
            parsed_hotkey.modifiers,
            parsed_hotkey.vk,
        )
        return parsed_hotkey.text

    def _message_loop(self, hotkey: ParsedHotkey) -> None:
        logging.info(
            "hotkey message loop starting hotkey=%s modifiers=%s vk=%s",
            hotkey.text,
            hotkey.modifiers,
            hotkey.vk,
        )
        if sys.platform != "win32":
            self.registration_error = RuntimeError("Win32 hotkey requires Windows")
            self.ready.set()
            return

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self.thread_id = kernel32.GetCurrentThreadId()
        logging.info("hotkey thread id=%s", self.thread_id)

        if not user32.RegisterHotKey(None, HOTKEY_ID, hotkey.modifiers, hotkey.vk):
            self.registration_error = ctypes.WinError()
            self.ready.set()
            return

        self.registered = True
        self.ready.set()
        msg = wintypes.MSG()
        try:
            while not self.stop_event.is_set():
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result == 0:
                    logging.info("hotkey message loop received WM_QUIT")
                    break
                if result == -1:
                    logging.error("hotkey GetMessageW failed: %s", ctypes.WinError())
                    break
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    logging.info("WM_HOTKEY received: %s", hotkey.text)
                    self.on_hotkey()
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)
            self.registered = False
            self.registered_hotkey = ""
            logging.info("hotkey unregistered and message loop ended: %s", hotkey.text)

    def unregister(self) -> None:
        logging.info(
            "unregister_hotkey requested registered=%s thread_id=%s",
            self.registered,
            self.thread_id,
        )
        if self.thread_id is not None:
            try:
                ctypes.windll.user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)
                logging.info("posted WM_QUIT to hotkey thread")
            except Exception:
                logging.exception("failed to post WM_QUIT to hotkey thread")
        if (
            self.thread is not None
            and self.thread.is_alive()
            and threading.current_thread() is not self.thread
        ):
            self.thread.join(timeout=1.0)
            logging.info("hotkey thread alive after join=%s", self.thread.is_alive())
        self.thread = None
        self.thread_id = None

