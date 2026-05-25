import importlib.util
import ctypes
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from datetime import datetime
from enum import Enum
from pathlib import Path
from ctypes import wintypes


WORKSPACE_ROOT = Path(__file__).resolve().parent
LOCAL_DIR = WORKSPACE_ROOT / ".local"
MODELS_DIR = LOCAL_DIR / "models"
SETTINGS_DIR = LOCAL_DIR / "settings"
LOGS_DIR = LOCAL_DIR / "logs"
WARMUP_AUDIO_PATH = WORKSPACE_ROOT / "assets" / "warmup.mp3"
LOG_FILE: Path | None = None
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
SW_RESTORE = 9
NIM_MODIFY = 0x00000001
NIF_INFO = 0x00000010
NIIF_INFO = 0x00000001
NIIF_RESPECT_QUIET_TIME = 0x00000080
ERROR_ALREADY_EXISTS = 183
CTRL_C_EVENT = 0
CTRL_BREAK_EVENT = 1
CTRL_CLOSE_EVENT = 2
SINGLE_INSTANCE_MUTEX_NAME = "Local\\WhisperTrayDictation"
SINGLE_INSTANCE_MUTEX_HANDLE = None
CONSOLE_CTRL_HANDLER = None
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


class NOTIFYICONDATAW(ctypes.Structure):
    class VERSION_OR_TIMEOUT(ctypes.Union):
        _fields_ = [
            ("uTimeout", wintypes.UINT),
            ("uVersion", wintypes.UINT),
        ]

    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("version_or_timeout", VERSION_OR_TIMEOUT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", wintypes.BYTE * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]
    _anonymous_ = ["version_or_timeout"]


def acquire_single_instance() -> bool:
    global SINGLE_INSTANCE_MUTEX_HANDLE
    if sys.platform != "win32":
        return True

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    handle = kernel32.CreateMutexW(None, True, SINGLE_INSTANCE_MUTEX_NAME)
    if not handle:
        print(f"failed to create single-instance mutex: {ctypes.WinError()}")
        return False

    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        print("Whisper Tray Dictation is already running.")
        kernel32.CloseHandle(handle)
        return False

    SINGLE_INSTANCE_MUTEX_HANDLE = handle
    return True


def install_console_ctrl_handler(app: "DictationApp") -> None:
    global CONSOLE_CTRL_HANDLER
    if sys.platform != "win32":
        return

    handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

    def handler(event_type: int) -> bool:
        if event_type in {CTRL_C_EVENT, CTRL_BREAK_EVENT, CTRL_CLOSE_EVENT}:
            app.request_exit_from_console(event_type)
            return True
        return False

    CONSOLE_CTRL_HANDLER = handler_type(handler)
    if not ctypes.windll.kernel32.SetConsoleCtrlHandler(CONSOLE_CTRL_HANDLER, True):
        logging.warning("SetConsoleCtrlHandler failed: %s", ctypes.WinError())
        return
    logging.info("console ctrl handler installed")


def uninstall_console_ctrl_handler() -> None:
    global CONSOLE_CTRL_HANDLER
    if sys.platform != "win32" or CONSOLE_CTRL_HANDLER is None:
        return
    if not ctypes.windll.kernel32.SetConsoleCtrlHandler(CONSOLE_CTRL_HANDLER, False):
        logging.warning("SetConsoleCtrlHandler uninstall failed: %s", ctypes.WinError())
    else:
        logging.info("console ctrl handler uninstalled")
    CONSOLE_CTRL_HANDLER = None


def setup_logging() -> None:
    global LOG_FILE
    if LOG_FILE is not None:
        return

    LOG_FILE = LOGS_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.log"
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(LOG_FILE),
        level=logging.DEBUG,
        format="%(asctime)s.%(msecs)03d [%(levelname)s] [%(threadName)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        encoding="utf-8",
        force=True,
    )
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s.%(msecs)03d [%(levelname)s] [%(threadName)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logging.getLogger().addHandler(stream_handler)
    for noisy_logger in (
        "PIL",
        "httpcore",
        "httpx",
        "huggingface_hub",
        "urllib3",
    ):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)
    logging.info("logging initialized: %s pid=%s", LOG_FILE, os.getpid())


def close_logging_for_local_delete() -> None:
    global LOG_FILE
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        try:
            handler.flush()
            handler.close()
        except Exception:
            pass
    LOG_FILE = None


SUPPORTED_PYTHON_MIN = (3, 9)
SUPPORTED_PYTHON_MAX = (3, 11)


def ensure_supported_python() -> None:
    version = sys.version_info[:2]
    logging.info("checking Python version: %s", sys.version.replace("\n", " "))
    if SUPPORTED_PYTHON_MIN <= version <= SUPPORTED_PYTHON_MAX:
        logging.info("Python version accepted")
        return

    min_version = ".".join(str(part) for part in SUPPORTED_PYTHON_MIN)
    max_version = ".".join(str(part) for part in SUPPORTED_PYTHON_MAX)
    current_version = ".".join(str(part) for part in version)
    raise RuntimeError(
        f"Unsupported Python {current_version}. "
        f"Use Python {min_version}-{max_version}; "
        "faster-whisper currently classifies support up to Python 3.11."
    )


ensure_supported_python()

logging.info("importing runtime dependencies")
import keyboard
import numpy as np
import pyperclip
import pystray
import sounddevice as sd


_NVIDIA_DLL_DIRECTORIES = []
_NVIDIA_DLL_HANDLES = []
_NVIDIA_DLL_DIRECTORIES_ADDED = set()
_NVIDIA_DLLS_PRELOADED = False


def add_nvidia_dll_directories() -> None:
    logging.info("adding NVIDIA DLL directories")
    if sys.platform != "win32" or not hasattr(os, "add_dll_directory"):
        logging.info("skipping NVIDIA DLL directories: platform=%s", sys.platform)
        return

    spec = importlib.util.find_spec("nvidia")
    if spec is None or spec.submodule_search_locations is None:
        logging.info("NVIDIA package not found")
        return

    nvidia_roots = [Path(path) for path in spec.submodule_search_locations]
    relative_dll_dirs = (
        Path("cublas") / "bin",
        Path("cuda_runtime") / "bin",
        Path("cuda_runtime") / "lib" / "x64",
        Path("cuda_nvrtc") / "bin",
        Path("cudnn") / "bin",
    )

    for nvidia_root in nvidia_roots:
        for relative_dll_dir in relative_dll_dirs:
            dll_dir = nvidia_root / relative_dll_dir
            if dll_dir.exists():
                add_path_directory(dll_dir)
                add_dll_directory_once(dll_dir)
                logging.info("added NVIDIA DLL directory: %s", dll_dir)

    venv_path = Path(sys.prefix)
    venv_nvidia_root = venv_path / "Lib" / "site-packages" / "nvidia"
    for relative_dll_dir in relative_dll_dirs:
        dll_dir = venv_nvidia_root / relative_dll_dir
        if dll_dir.exists():
            add_path_directory(dll_dir)
            add_dll_directory_once(dll_dir)
            logging.info("added venv NVIDIA DLL directory: %s", dll_dir)


def add_package_dll_directories() -> None:
    logging.info("adding package DLL directories")
    if sys.platform != "win32" or not hasattr(os, "add_dll_directory"):
        logging.info("skipping package DLL directories: platform=%s", sys.platform)
        return

    for package_name in (
        "ctranslate2",
        "onnxruntime",
    ):
        spec = importlib.util.find_spec(package_name)
        if spec is None or spec.origin is None:
            logging.info("package not found for DLL directory: %s", package_name)
            continue

        dll_dir = Path(spec.origin).resolve().parent
        if dll_dir.exists():
            add_path_directory(dll_dir)
            add_dll_directory_once(dll_dir)
            logging.info("added package DLL directory for %s: %s", package_name, dll_dir)


def add_dll_directory_once(directory: Path) -> None:
    directory_text = str(directory.resolve()).lower()
    if directory_text in _NVIDIA_DLL_DIRECTORIES_ADDED:
        return
    _NVIDIA_DLL_DIRECTORIES.append(os.add_dll_directory(str(directory)))
    _NVIDIA_DLL_DIRECTORIES_ADDED.add(directory_text)


def add_path_directory(directory: Path) -> None:
    current_path = os.environ.get("PATH", "")
    directory_text = str(directory)
    path_parts = [part for part in current_path.split(os.pathsep) if part]
    if any(part.lower() == directory_text.lower() for part in path_parts):
        return
    os.environ["PATH"] = directory_text + os.pathsep + current_path
    logging.debug("prepended PATH directory: %s", directory)


def preload_nvidia_dlls() -> None:
    global _NVIDIA_DLLS_PRELOADED
    if sys.platform != "win32":
        return
    if _NVIDIA_DLLS_PRELOADED:
        logging.debug("NVIDIA DLLs already preloaded")
        return

    dll_names = (
        "cudart64_12.dll",
        "nvrtc64_120_0.dll",
        "nvrtc-builtins64_129.dll",
        "cublas64_12.dll",
        "cublasLt64_12.dll",
        "cudnn64_9.dll",
        "cudnn_ops64_9.dll",
        "cudnn_adv64_9.dll",
        "cudnn_cnn64_9.dll",
        "cudnn_graph64_9.dll",
        "cudnn_heuristic64_9.dll",
        "cudnn_engines_precompiled64_9.dll",
        "cudnn_engines_runtime_compiled64_9.dll",
        "cudnn_engines_tensor_ir64_9.dll",
        "cudnn_ext64_9.dll",
    )
    search_roots = [
        Path(sys.prefix) / "Lib" / "site-packages" / "nvidia",
    ]
    spec = importlib.util.find_spec("nvidia")
    if spec is not None and spec.submodule_search_locations is not None:
        search_roots.extend(Path(path) for path in spec.submodule_search_locations)

    for dll_name in dll_names:
        dll_path = next(
            (
                path
                for root in search_roots
                for path in root.rglob(dll_name)
                if path.exists()
            ),
            None,
        )
        if dll_path is None:
            logging.warning("NVIDIA DLL not found for preload: %s", dll_name)
            continue
        try:
            _NVIDIA_DLL_HANDLES.append(ctypes.WinDLL(str(dll_path)))
            logging.info("preloaded NVIDIA DLL: %s", dll_path)
        except Exception:
            logging.exception("failed to preload NVIDIA DLL: %s", dll_path)
    _NVIDIA_DLLS_PRELOADED = True


add_nvidia_dll_directories()
add_package_dll_directories()
preload_nvidia_dlls()

from faster_whisper import WhisperModel
from faster_whisper.utils import available_models, download_model as download_faster_whisper_model
from PIL import Image, ImageDraw
logging.info("runtime dependencies imported")


APP_NAME = "Whisper Tray Dictation"
DEFAULT_HOTKEY = "ctrl+shift+space"
SAMPLE_RATE = 16000
CHANNELS = 1
DEFAULT_MODEL_NAME = "large-v3"
SUPPORTED_LANGUAGES = ("auto", "en", "pl")
PASTED_MESSAGE = "pasted transcript in active input"
CLIPBOARD_MESSAGE = "transcript  in clipboard"
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


class AppState(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    DOWNLOADING_MODEL = "downloading_model"
    WARMING_UP = "warming_up"


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


def parse_hotkey(hotkey: str) -> tuple[int, int, str] | None:
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
    return modifier_flags, vk, canonical_hotkey_text(modifiers, key)


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


class Config:
    def __init__(self) -> None:
        self.path = SETTINGS_DIR / "config.json"
        self.language = "auto"
        self.model_name = DEFAULT_MODEL_NAME
        self.hotkey = DEFAULT_HOTKEY
        logging.info("config initialized with path=%s", self.path)
        self.load()

    def load(self) -> None:
        logging.info("loading config from %s", self.path)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            logging.info("config file not found; using defaults")
            return
        except Exception:
            logging.exception("failed to load config; using defaults")
            return

        language = data.get("language")
        if language in SUPPORTED_LANGUAGES:
            self.language = language
            logging.info("loaded language=%s", language)
        elif language is not None:
            logging.warning("ignored unsupported language in config: %s", language)

        model_name = data.get("model_name")
        if model_name in get_available_model_names():
            self.model_name = model_name
            logging.info("loaded model_name=%s", model_name)
        elif model_name is not None:
            logging.warning("ignored unsupported model_name in config: %s", model_name)

        hotkey = data.get("hotkey")
        parsed_hotkey = parse_hotkey(hotkey) if isinstance(hotkey, str) else None
        if parsed_hotkey is not None:
            self.hotkey = parsed_hotkey[2]
            logging.info("loaded hotkey=%s", self.hotkey)
        elif hotkey is not None:
            logging.warning("ignored unsupported hotkey in config: %s", hotkey)

    def save(self) -> None:
        logging.info(
            "saving config path=%s language=%s model_name=%s hotkey=%s",
            self.path,
            self.language,
            self.model_name,
            self.hotkey,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "language": self.language,
                    "model_name": self.model_name,
                    "hotkey": self.hotkey,
                },
                indent=2,
            ),
            encoding="utf-8",
        )


def get_available_model_names() -> tuple[str, ...]:
    logging.info("loading faster-whisper available model names")
    names = tuple(available_models())
    model_names = (DEFAULT_MODEL_NAME, *(name for name in names if name != DEFAULT_MODEL_NAME))
    logging.info("available model names: %s", ", ".join(model_names))
    return model_names


class DictationApp:
    def __init__(self) -> None:
        logging.info("DictationApp init start")
        self.config = Config()
        self.model_names = get_available_model_names()
        self.icon: pystray.Icon | None = None
        self.state = AppState.IDLE
        self.state_lock = threading.RLock()
        self.frames: list[np.ndarray] = []
        self.stream: sd.InputStream | None = None
        self.model: WhisperModel | None = None
        self.model_device = ""
        self.gpu_disabled = False
        self.target_window_hwnd: int | None = None
        self.last_toggle_at = 0.0
        self.hotkey_registered = False
        self.hotkey_thread: threading.Thread | None = None
        self.hotkey_thread_id: int | None = None
        self.hotkey_ready = threading.Event()
        self.hotkey_registration_error: Exception | None = None
        self.registered_hotkey = ""
        self.native_balloon_available = True
        self.stop_event = threading.Event()
        self.exit_lock = threading.Lock()
        self.exiting = False
        self.restart_requested = False
        logging.info(
            "DictationApp init complete state=%s language=%s model=%s hotkey=%s",
            self.state.value,
            self.config.language,
            self.config.model_name,
            self.config.hotkey,
        )

    def run(self) -> None:
        logging.info("app run start platform=%s", sys.platform)
        if sys.platform != "win32":
            raise RuntimeError("This application is Windows-only.")

        needs_initial_model = not self.has_downloaded_models()
        logging.info("needs_initial_model=%s", needs_initial_model)

        logging.info("creating tray icon")
        self.icon = pystray.Icon(
            "whisper_tray_dictation",
            self.make_icon(recording=False),
            APP_NAME,
            self.make_menu(),
        )
        logging.info("starting tray icon run loop")
        self.icon.run(
            setup=lambda icon: self.finish_startup(icon, needs_initial_model),
        )
        logging.info("tray icon run loop ended")

    def finish_startup(self, icon: pystray.Icon, needs_initial_model: bool) -> None:
        logging.info("finish_startup needs_initial_model=%s", needs_initial_model)
        icon.visible = True
        logging.info("tray icon visible=%s", icon.visible)

        if needs_initial_model:
            logging.info("no downloaded models found; showing initial model dialog")
            initial_model_name = self.show_initial_model_dialog()
            logging.info("initial model dialog selected model=%s", initial_model_name)
            self.config.model_name = initial_model_name
            self.config.save()
            logging.info("starting first model download thread")
            threading.Thread(
                target=self.download_selected_model,
                args=(initial_model_name,),
                name="first-model-download",
                daemon=True,
            ).start()
            return
        self.start_warmup_then_register()

    def register_hotkey(self) -> None:
        logging.info("register_hotkey requested already_registered=%s", self.hotkey_registered)
        if self.hotkey_registered:
            return

        parsed_hotkey = parse_hotkey(self.config.hotkey)
        if parsed_hotkey is None:
            logging.warning("configured hotkey invalid; falling back to %s", DEFAULT_HOTKEY)
            self.config.hotkey = DEFAULT_HOTKEY
            self.config.save()
            parsed_hotkey = parse_hotkey(DEFAULT_HOTKEY)
        if parsed_hotkey is None:
            logging.error("default hotkey is invalid: %s", DEFAULT_HOTKEY)
            return

        modifiers, vk, hotkey_text = parsed_hotkey

        self.hotkey_ready.clear()
        self.hotkey_registration_error = None
        self.hotkey_thread = threading.Thread(
            target=self.hotkey_message_loop,
            args=(hotkey_text, modifiers, vk),
            name="win32-hotkey",
            daemon=True,
        )
        self.hotkey_thread.start()
        if not self.hotkey_ready.wait(timeout=2.0):
            logging.error("hotkey registration timed out: %s", hotkey_text)
            return
        if self.hotkey_registration_error is not None:
            logging.error(
                "hotkey registration failed: %s error=%s",
                hotkey_text,
                self.hotkey_registration_error,
            )
            return
        self.registered_hotkey = hotkey_text
        logging.info("hotkey registered: %s modifiers=%s vk=%s", hotkey_text, modifiers, vk)

    def hotkey_message_loop(self, hotkey_text: str, modifiers: int, vk: int) -> None:
        logging.info(
            "hotkey message loop starting hotkey=%s modifiers=%s vk=%s",
            hotkey_text,
            modifiers,
            vk,
        )
        if sys.platform != "win32":
            self.hotkey_registration_error = RuntimeError("Win32 hotkey requires Windows")
            self.hotkey_ready.set()
            return

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self.hotkey_thread_id = kernel32.GetCurrentThreadId()
        logging.info("hotkey thread id=%s", self.hotkey_thread_id)

        if not user32.RegisterHotKey(None, HOTKEY_ID, modifiers, vk):
            self.hotkey_registration_error = ctypes.WinError()
            self.hotkey_ready.set()
            return

        self.hotkey_registered = True
        self.hotkey_ready.set()
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
                    logging.info("WM_HOTKEY received: %s", hotkey_text)
                    self.toggle_recording()
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)
            self.hotkey_registered = False
            self.registered_hotkey = ""
            logging.info("hotkey unregistered and message loop ended: %s", hotkey_text)

    def unregister_hotkey(self) -> None:
        logging.info(
            "unregister_hotkey requested registered=%s thread_id=%s",
            self.hotkey_registered,
            self.hotkey_thread_id,
        )
        if self.hotkey_thread_id is not None:
            try:
                ctypes.windll.user32.PostThreadMessageW(
                    self.hotkey_thread_id,
                    WM_QUIT,
                    0,
                    0,
                )
                logging.info("posted WM_QUIT to hotkey thread")
            except Exception:
                logging.exception("failed to post WM_QUIT to hotkey thread")
        if (
            self.hotkey_thread is not None
            and self.hotkey_thread.is_alive()
            and threading.current_thread() is not self.hotkey_thread
        ):
            self.hotkey_thread.join(timeout=1.0)
            logging.info("hotkey thread alive after join=%s", self.hotkey_thread.is_alive())
        self.hotkey_thread = None
        self.hotkey_thread_id = None

    def make_menu(self) -> pystray.Menu:
        logging.debug("building tray menu")
        return pystray.Menu(
            pystray.MenuItem(
                "lang",
                pystray.Menu(*self.make_language_items()),
            ),
            pystray.MenuItem(
                "model",
                pystray.Menu(*self.make_model_items()),
            ),
            pystray.MenuItem(
                "set shortcut",
                self.set_shortcut,
                enabled=lambda _item: self.state == AppState.IDLE,
            ),
            pystray.MenuItem(
                "debug",
                pystray.Menu(
                    pystray.MenuItem(
                        "remove local data",
                        self.remove_local_data,
                        enabled=lambda _item: self.state == AppState.IDLE,
                    ),
                    pystray.MenuItem("open logs directory", self.open_logs_directory),
                ),
            ),
            pystray.MenuItem("restart", self.restart_app),
            pystray.MenuItem("exit", self.exit_app),
        )

    def make_language_items(self) -> list[pystray.MenuItem]:
        logging.debug("building language menu items")
        def make_action(language: str):
            def action(_icon, _item) -> None:
                logging.info("language menu action selected=%s", language)
                self.set_language(language)

            return action

        def make_checked(language: str):
            def checked(_item) -> bool:
                return self.config.language == language

            return checked

        return [
            pystray.MenuItem(
                language,
                make_action(language),
                checked=make_checked(language),
                radio=True,
            )
            for language in SUPPORTED_LANGUAGES
        ]

    def make_model_items(self) -> list[pystray.MenuItem]:
        logging.debug("building model menu items")
        def make_action(model_name: str):
            def action(_icon, _item) -> None:
                logging.info("model menu action selected=%s", model_name)
                self.set_model(model_name)

            return action

        def make_checked(model_name: str):
            def checked(_item) -> bool:
                return self.config.model_name == model_name

            return checked

        return [
            pystray.MenuItem(
                model_name,
                make_action(model_name),
                checked=make_checked(model_name),
                enabled=lambda _item: self.state == AppState.IDLE,
                radio=True,
            )
            for model_name in self.model_names
        ]

    def make_icon(self, recording: bool) -> Image.Image:
        logging.debug("creating tray icon image recording=%s", recording)
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        color = (220, 20, 40, 255) if recording else (255, 255, 255, 255)
        outline = (70, 70, 70, 255) if not recording else (120, 0, 0, 255)
        draw.ellipse((10, 10, 54, 54), fill=color, outline=outline, width=3)
        return image

    def set_language(self, language: str) -> None:
        logging.info("set_language requested language=%s", language)
        if language not in SUPPORTED_LANGUAGES:
            logging.warning("ignored unsupported language=%s", language)
            return
        self.config.language = language
        self.config.save()
        if self.icon:
            self.icon.update_menu()
        logging.info("language updated to %s", language)

    def set_model(self, model_name: str) -> None:
        logging.info("set_model requested model_name=%s", model_name)
        if model_name not in self.model_names:
            logging.warning("ignored unsupported model_name=%s", model_name)
            return
        should_download_first_model = False
        with self.state_lock:
            logging.info("set_model current state=%s", self.state.value)
            if self.state != AppState.IDLE:
                self.notify("Cannot change model while recording or transcribing.")
                return
            should_download_first_model = not self.has_downloaded_models()
            logging.info("set_model should_download_first_model=%s", should_download_first_model)
            if self.config.model_name == model_name and not should_download_first_model:
                logging.info("set_model ignored: model already selected")
                return
            self.config.model_name = model_name
            self.config.save()
            self.model = None
            self.model_device = ""
            self.gpu_disabled = False
        if self.icon:
            self.icon.update_menu()
        if should_download_first_model:
            logging.info("starting first model download from model menu")
            threading.Thread(
                target=self.download_selected_model,
                args=(model_name,),
                name="model-menu-download",
                daemon=True,
            ).start()
            return

        if self.hotkey_registered:
            self.unregister_hotkey()
        self.start_warmup_then_register()

    def set_shortcut(self, _icon=None, _item=None) -> None:
        logging.info("set_shortcut requested")
        with self.state_lock:
            if self.state != AppState.IDLE:
                self.notify("Cannot change shortcut while recording or transcribing.")
                return

        was_registered = self.hotkey_registered
        if was_registered:
            self.unregister_hotkey()

        selected_hotkey = self.show_shortcut_dialog()
        if selected_hotkey is None:
            logging.info("set_shortcut cancelled")
            if was_registered:
                self.register_hotkey()
            return

        parsed_hotkey = parse_hotkey(selected_hotkey)
        if parsed_hotkey is None:
            logging.warning("set_shortcut rejected invalid hotkey=%s", selected_hotkey)
            self.notify("invalid shortcut")
            if was_registered:
                self.register_hotkey()
            return

        normalized_hotkey = parsed_hotkey[2]
        logging.info("set_shortcut accepted hotkey=%s", normalized_hotkey)

        self.config.hotkey = normalized_hotkey
        self.config.save()
        if self.icon:
            self.icon.update_menu()

        if was_registered:
            self.register_hotkey()
            if not self.hotkey_registered:
                self.notify(f"shortcut registration failed: {normalized_hotkey}")
                return
        self.notify(f"shortcut set to {normalized_hotkey}")

    def restart_app(self, _icon=None, _item=None) -> None:
        logging.info("restart_app requested")
        self.restart_requested = True
        self.exit_app()

    def remove_local_data(self, _icon=None, _item=None) -> None:
        logging.info("remove_local_data requested")
        with self.state_lock:
            if self.state != AppState.IDLE:
                self.notify("Cannot remove local data while recording or transcribing.")
                return
            self.state = AppState.DOWNLOADING_MODEL
            self.refresh_icon()

        self.unregister_hotkey()
        self.stop_stream()
        self.model = None
        self.model_device = ""
        self.gpu_disabled = False

        import gc

        gc.collect()
        close_logging_for_local_delete()
        try:
            if LOCAL_DIR.exists():
                shutil.rmtree(LOCAL_DIR)
        except Exception as exc:
            setup_logging()
            logging.exception("failed to remove local data directory")
            with self.state_lock:
                self.state = AppState.IDLE
                self.refresh_icon()
            self.notify(f"failed to remove local data: {exc}")
            return

        setup_logging()
        logging.info("local data removed path=%s", LOCAL_DIR)
        self.config = Config()
        self.model_names = get_available_model_names()
        if self.icon:
            self.icon.update_menu()

        with self.state_lock:
            self.state = AppState.IDLE
            self.refresh_icon()

        model_name = self.show_initial_model_dialog()
        logging.info("remove_local_data selected model=%s", model_name)
        self.config.model_name = model_name
        self.config.save()
        threading.Thread(
            target=self.download_selected_model,
            args=(model_name,),
            name="debug-local-data-download",
            daemon=True,
        ).start()

    def open_logs_directory(self, _icon=None, _item=None) -> None:
        logging.info("open_logs_directory requested")
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(str(LOGS_DIR))
        except Exception as exc:
            logging.exception("failed to open logs directory")
            self.notify(f"failed to open logs directory: {exc}")

    def exit_app(self, _icon=None, _item=None) -> None:
        logging.info("exit_app requested")
        with self.exit_lock:
            if self.exiting:
                logging.info("exit_app ignored: already exiting")
                return
            self.exiting = True

        try:
            self.stop_event.set()
            self.unregister_hotkey()
            with self.state_lock:
                if self.stream is not None:
                    self.stop_stream()
                self.state = AppState.IDLE
                logging.info("state set to idle during exit")
            if self.icon:
                self.icon.stop()
                logging.info("tray icon stop requested")
        except Exception:
            logging.exception("exit_app failed")

    def start_replacement_process(self) -> None:
        logging.info("start_replacement_process requested")
        args = [sys.executable, str(Path(__file__).resolve())]
        try:
            subprocess.Popen(
                args,
                cwd=str(WORKSPACE_ROOT),
                close_fds=True,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            logging.info("replacement process started args=%s", args)
        except Exception:
            logging.exception("failed to start replacement process args=%s", args)

    def request_exit_from_console(self, event_type: int) -> None:
        logging.info("console control event received: %s", event_type)
        threading.Thread(
            target=self.exit_app,
            name="console-exit",
            daemon=True,
        ).start()

    def toggle_recording(self) -> None:
        logging.info("toggle_recording invoked")
        with self.state_lock:
            if not self.hotkey_registered or not self.has_downloaded_models():
                logging.info(
                    "toggle ignored hotkey_registered=%s has_downloaded_models=%s",
                    self.hotkey_registered,
                    self.has_downloaded_models(),
                )
                return

            now = time.monotonic()
            if now - self.last_toggle_at < 0.6:
                logging.info("toggle ignored by debounce elapsed=%.3f", now - self.last_toggle_at)
                return
            self.last_toggle_at = now

            if self.state == AppState.IDLE:
                logging.info("toggle starting recording")
                self.target_window_hwnd = self.get_foreground_window()
                logging.info("captured target_window_hwnd=%s", self.target_window_hwnd)
                self.start_recording()
                return
            if self.state == AppState.RECORDING:
                logging.info("toggle stopping recording")
                audio = self.stop_recording()
                self.state = AppState.TRANSCRIBING
                logging.info("state changed to transcribing audio_samples=%s", len(audio))
                self.refresh_icon()
                threading.Thread(
                    target=self.transcribe_and_deliver,
                    args=(audio,),
                    name="transcription",
                    daemon=True,
                ).start()

    def start_recording(self) -> None:
        logging.info("start_recording")
        self.frames = []
        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            callback=self.audio_callback,
        )
        self.stream.start()
        self.state = AppState.RECORDING
        logging.info("recording stream started sample_rate=%s channels=%s", SAMPLE_RATE, CHANNELS)
        self.refresh_icon()

    def audio_callback(self, indata, _frames, _time_info, status) -> None:
        if status:
            print(status, file=sys.stderr)
            logging.warning("audio callback status=%s", status)
        with self.state_lock:
            if self.state == AppState.RECORDING:
                self.frames.append(indata.copy())

    def stop_recording(self) -> np.ndarray:
        logging.info("stop_recording frames=%s", len(self.frames))
        self.stop_stream()
        if not self.frames:
            logging.info("recording stopped with no frames")
            return np.zeros((0, CHANNELS), dtype=np.float32)
        audio = np.concatenate(self.frames, axis=0)
        logging.info("recording stopped audio_shape=%s", audio.shape)
        return audio

    def stop_stream(self) -> None:
        logging.info("stop_stream stream_exists=%s", self.stream is not None)
        if self.stream is None:
            return
        try:
            self.stream.stop()
            self.stream.close()
            logging.info("audio stream stopped and closed")
        finally:
            self.stream = None

    def transcribe_and_deliver(self, audio: np.ndarray) -> None:
        logging.info("transcribe_and_deliver start audio_shape=%s", getattr(audio, "shape", None))
        try:
            if audio.size == 0:
                self.notify("No audio recorded.")
                return

            wav_path = self.write_temp_wav(audio)
            logging.info("temporary wav created path=%s", wav_path)
            try:
                text = self.transcribe_file(wav_path)
            finally:
                try:
                    os.remove(wav_path)
                    logging.info("temporary wav removed path=%s", wav_path)
                except OSError:
                    logging.exception("failed to remove temporary wav path=%s", wav_path)

            if not text:
                self.notify("No speech detected.")
                return

            logging.info("transcription text length=%s", len(text))
            delivered_to_input = self.deliver_text(text)
            logging.info("deliver_text result delivered_to_input=%s", delivered_to_input)
            self.notify(PASTED_MESSAGE if delivered_to_input else CLIPBOARD_MESSAGE)
        except Exception as exc:
            logging.exception("transcribe_and_deliver failed")
            self.notify(f"Transcription failed: {exc}")
        finally:
            with self.state_lock:
                self.state = AppState.IDLE
                logging.info("state changed to idle after transcription")
                self.refresh_icon()

    def write_temp_wav(self, audio: np.ndarray) -> str:
        logging.info("write_temp_wav audio_shape=%s", getattr(audio, "shape", None))
        audio = np.clip(audio.reshape(-1), -1.0, 1.0)
        pcm = (audio * 32767).astype(np.int16)
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        handle.close()
        with wave.open(handle.name, "wb") as wav_file:
            wav_file.setnchannels(CHANNELS)
            wav_file.setsampwidth(2)
            wav_file.setframerate(SAMPLE_RATE)
            wav_file.writeframes(pcm.tobytes())
        return handle.name

    def transcribe_file(self, wav_path: str) -> str:
        logging.info("transcribe_file path=%s language=%s model=%s", wav_path, self.config.language, self.config.model_name)
        language = None if self.config.language == "auto" else self.config.language
        try:
            text = self.transcribe_with_current_model(wav_path, language)
        except RuntimeError as exc:
            if self.model_device != "cuda":
                raise
            logging.exception("GPU transcription failed; retrying on CPU")
            self.notify("gpu inference not available, running on cpu")
            self.gpu_disabled = True
            self.model = None
            self.model_device = ""
            text = self.transcribe_with_current_model(wav_path, language)

        logging.info("transcribe_file complete text_length=%s", len(text))
        return text

    def transcribe_with_current_model(self, wav_path: str, language: str | None) -> str:
        logging.info(
            "transcribe_with_current_model path=%s language=%s model_device=%s gpu_disabled=%s",
            wav_path,
            language or "auto",
            self.model_device or "not_loaded",
            self.gpu_disabled,
        )
        model = self.get_model()
        segments, _info = model.transcribe(
            wav_path,
            language=language,
            vad_filter=True,
            beam_size=5,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()

    def get_model(self) -> WhisperModel:
        logging.info("get_model requested current_model_loaded=%s", self.model is not None)
        if self.model is not None:
            return self.model

        add_nvidia_dll_directories()
        add_package_dll_directories()
        preload_nvidia_dlls()

        if not self.gpu_disabled and self.cuda_device_0_available():
            logging.info("CUDA device 0 available; loading model on GPU")
            try:
                self.model = WhisperModel(
                    self.config.model_name,
                    device="cuda",
                    device_index=0,
                    compute_type="float16",
                    download_root=str(MODELS_DIR),
                )
                self.model_device = "cuda"
                logging.info("model loaded on GPU model=%s", self.config.model_name)
                return self.model
            except Exception:
                logging.exception("failed to load model on GPU; falling back to CPU")
                self.gpu_disabled = True
                self.notify("gpu inference not available, running on cpu")
        else:
            if self.gpu_disabled:
                logging.info("GPU disabled for this session; loading model on CPU")
            else:
                logging.info("CUDA device 0 unavailable; loading model on CPU")
                self.notify("gpu inference not available, running on cpu")

        logging.info("loading model on CPU model=%s", self.config.model_name)
        self.model = WhisperModel(
            self.config.model_name,
            device="cpu",
            compute_type="int8",
            download_root=str(MODELS_DIR),
        )
        self.model_device = "cpu"
        logging.info("model loaded on CPU model=%s", self.config.model_name)
        return self.model

    def has_downloaded_models(self) -> bool:
        logging.debug("checking downloaded models in %s", MODELS_DIR)
        if not MODELS_DIR.exists():
            logging.debug("models dir does not exist")
            return False
        result = any(MODELS_DIR.rglob("model.bin"))
        logging.debug("has_downloaded_models=%s", result)
        return result

    def download_selected_model(self, model_name: str) -> None:
        logging.info("download_selected_model start model_name=%s", model_name)
        with self.state_lock:
            if self.state != AppState.IDLE:
                logging.info("download ignored because state=%s", self.state.value)
                return
            self.state = AppState.DOWNLOADING_MODEL
            logging.info("state changed to downloading_model")
            self.refresh_icon()

        self.notify(f"downloading model {model_name}")
        try:
            MODELS_DIR.mkdir(parents=True, exist_ok=True)
            logging.info("models dir ensured path=%s", MODELS_DIR)
            download_faster_whisper_model(model_name, cache_dir=str(MODELS_DIR))
        except Exception as exc:
            logging.exception("download failed model_name=%s", model_name)
            self.notify(f"error downloading model {model_name}: {exc}")
            with self.state_lock:
                self.state = AppState.IDLE
                logging.info("state changed to idle after failed download")
                self.refresh_icon()
            return

        logging.info("download completed model_name=%s", model_name)
        self.config.model_name = model_name
        self.config.save()
        self.notify(f"downloaded model {model_name}")
        self.start_warmup_then_register()

    def start_warmup_then_register(self) -> None:
        logging.info("start_warmup_then_register requested")
        threading.Thread(
            target=self.warmup_then_register,
            name="model-warmup",
            daemon=True,
        ).start()

    def warmup_then_register(self) -> None:
        logging.info("warmup_then_register start")
        warmup_succeeded = False
        with self.state_lock:
            if self.state not in {AppState.IDLE, AppState.DOWNLOADING_MODEL}:
                logging.info("warmup skipped because state=%s", self.state.value)
                return
            self.state = AppState.WARMING_UP
            logging.info("state changed to warming_up")
            self.refresh_icon()

        try:
            if not WARMUP_AUDIO_PATH.exists():
                logging.warning("warmup audio missing: %s", WARMUP_AUDIO_PATH)
                self.notify("warmup audio missing")
                return

            self.notify(f"warming up model {self.config.model_name}")
            text = self.transcribe_file(str(WARMUP_AUDIO_PATH))
            logging.info("warmup transcription discarded text_length=%s", len(text))
            warmup_succeeded = True
            self.notify(f"model ready {self.config.model_name}")
        except Exception as exc:
            logging.exception("model warmup failed")
            self.notify(f"model warmup failed: {exc}")
        finally:
            with self.state_lock:
                self.state = AppState.IDLE
                logging.info("state changed to idle after warmup")
                self.refresh_icon()
            if warmup_succeeded and not self.exiting:
                self.register_hotkey()

    def show_initial_model_dialog(self) -> str:
        logging.info("show_initial_model_dialog start")
        import tkinter as tk
        from tkinter import ttk

        root = tk.Tk()
        root.title(APP_NAME)
        root.resizable(False, False)
        root.attributes("-topmost", True)

        selected_model = tk.StringVar(value=self.model_names[0])
        chosen_model = {"name": self.model_names[0]}

        frame = ttk.Frame(root, padding=16)
        frame.grid(row=0, column=0, sticky="nsew")

        message = (
            "No model is downloaded in .local/models.\n"
            "Choose the model to download before dictation can be used."
        )
        ttk.Label(frame, text=message, justify="left").grid(
            row=0,
            column=0,
            sticky="w",
            pady=(0, 12),
        )

        list_frame = ttk.Frame(frame)
        list_frame.grid(row=1, column=0, sticky="nsew")

        for index, model_name in enumerate(self.model_names):
            ttk.Radiobutton(
                list_frame,
                text=model_name,
                value=model_name,
                variable=selected_model,
            ).grid(row=index, column=0, sticky="w", pady=1)

        def accept() -> None:
            chosen_model["name"] = selected_model.get()
            logging.info("initial model dialog accepted model=%s", chosen_model["name"])
            root.destroy()

        ttk.Button(frame, text="OK", command=accept).grid(
            row=2,
            column=0,
            sticky="e",
            pady=(12, 0),
        )
        root.protocol("WM_DELETE_WINDOW", accept)
        root.bind("<Return>", lambda _event: accept())
        root.update_idletasks()

        width = root.winfo_width()
        height = root.winfo_height()
        x = (root.winfo_screenwidth() - width) // 2
        y = (root.winfo_screenheight() - height) // 2
        root.geometry(f"+{x}+{y}")
        root.mainloop()

        logging.info("show_initial_model_dialog end selected=%s", chosen_model["name"])
        return chosen_model["name"]

    def show_shortcut_dialog(self) -> str | None:
        logging.info("show_shortcut_dialog start current_hotkey=%s", self.config.hotkey)
        import tkinter as tk
        from tkinter import ttk

        root = tk.Tk()
        root.title("set shortcut")
        root.resizable(False, False)
        root.attributes("-topmost", True)

        captured_hotkey = {"value": self.config.hotkey}
        result = {"value": None}
        current_text = tk.StringVar(value=self.config.hotkey)

        frame = ttk.Frame(root, padding=12)
        frame.grid(row=0, column=0, sticky="nsew")

        ttk.Label(
            frame,
            text="press keyboard shorctu combination and Enter to confirm",
            justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))

        entry = ttk.Entry(frame, textvariable=current_text, width=36)
        entry.grid(row=1, column=0, sticky="ew")

        def capture(event) -> str:
            keysym = (event.keysym or "").lower()
            if keysym in {"return", "enter"}:
                return accept()
            if keysym == "escape":
                return cancel()

            key_name = key_name_from_tk_event(event)
            if key_name is None:
                return "break"

            modifiers = get_pressed_modifier_names()
            if not modifiers:
                logging.info("shortcut dialog ignored key without modifier: %s", key_name)
                return "break"

            hotkey = canonical_hotkey_text(modifiers, key_name)
            if parse_hotkey(hotkey) is None:
                logging.info("shortcut dialog ignored invalid hotkey candidate: %s", hotkey)
                return "break"

            captured_hotkey["value"] = hotkey
            current_text.set(hotkey)
            entry.icursor(tk.END)
            logging.info("shortcut dialog captured hotkey=%s", hotkey)
            return "break"

        def accept(_event=None) -> str:
            result["value"] = captured_hotkey["value"]
            logging.info("shortcut dialog accepted hotkey=%s", result["value"])
            root.destroy()
            return "break"

        def cancel(_event=None) -> str:
            logging.info("shortcut dialog cancelled")
            result["value"] = None
            root.destroy()
            return "break"

        entry.bind("<KeyPress>", capture)
        root.bind("<Return>", accept)
        root.bind("<Escape>", cancel)
        root.protocol("WM_DELETE_WINDOW", cancel)

        root.update_idletasks()
        width = root.winfo_width()
        height = root.winfo_height()
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        x = max(0, screen_width - width - 24)
        y = max(0, screen_height - height - 96)
        root.geometry(f"+{x}+{y}")
        entry.focus_set()
        root.mainloop()

        logging.info("show_shortcut_dialog end selected=%s", result["value"])
        return result["value"]

    def cuda_device_0_available(self) -> bool:
        logging.info("checking CUDA device count")
        try:
            import ctranslate2

            count = ctranslate2.get_cuda_device_count()
            logging.info("CUDA device count=%s", count)
            return count > 0
        except Exception:
            logging.exception("failed to check CUDA device count")
            return False

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

    def refresh_icon(self) -> None:
        logging.debug("refresh_icon state=%s icon_exists=%s", self.state.value, self.icon is not None)
        if not self.icon:
            return
        self.icon.icon = self.make_icon(recording=self.state != AppState.IDLE)
        self.icon.update_menu()
        logging.debug("tray icon refreshed")

    def notify(self, message: str) -> None:
        logging.info("notify: %s", message)
        print(message)
        if not self.icon:
            logging.info("notify skipped tray notification: icon does not exist")
            return
        try:
            self.icon.notify(message, APP_NAME)
            logging.info("pystray notification requested")
        except Exception:
            logging.exception("tray notification failed: %s", message)
        self.show_native_balloon(message)
        self.show_tray_popup(message)

    def show_native_balloon(self, message: str) -> None:
        if sys.platform != "win32" or not self.icon:
            return
        if not self.native_balloon_available:
            logging.info("native balloon skipped: disabled after previous failure")
            return
        hwnd = getattr(self.icon, "_hwnd", None)
        if not hwnd:
            logging.info("native balloon skipped: missing icon hwnd")
            return
        try:
            data = NOTIFYICONDATAW()
            data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            data.hWnd = hwnd
            data.uID = id(self.icon)
            data.uFlags = NIF_INFO
            data.szInfo = message[:255]
            data.szInfoTitle = APP_NAME[:63]
            data.dwInfoFlags = NIIF_INFO
            if ctypes.windll.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data)):
                logging.info("native tray balloon requested")
            else:
                self.native_balloon_available = False
                logging.warning("native tray balloon disabled after failure: %s", ctypes.WinError())
        except Exception:
            self.native_balloon_available = False
            logging.exception("native tray balloon failed with exception")

    def show_tray_popup(self, message: str) -> None:
        logging.info("show_tray_popup requested: %s", message)
        threading.Thread(
            target=self.run_tray_popup,
            args=(message,),
            name="tray-popup",
            daemon=True,
        ).start()

    def run_tray_popup(self, message: str) -> None:
        logging.info("run_tray_popup start")
        try:
            import tkinter as tk

            root = tk.Tk()
            root.title(APP_NAME)
            root.resizable(False, False)
            root.attributes("-topmost", True)
            root.overrideredirect(True)
            transparent_color = "#ff00ff"
            root.configure(bg=transparent_color)
            try:
                root.attributes("-transparentcolor", transparent_color)
            except Exception:
                logging.exception("tray popup transparent color unavailable")

            padding_x = 14
            padding_y = 10
            radius = 3
            canvas = tk.Canvas(
                root,
                bg=transparent_color,
                highlightthickness=0,
                borderwidth=0,
            )
            canvas.grid(row=0, column=0, sticky="nsew")
            text_id = canvas.create_text(
                padding_x,
                padding_y,
                anchor="nw",
                fill="#111111",
                text=message,
                width=360,
            )
            text_bbox = canvas.bbox(text_id) or (0, 0, 1, 1)
            width = text_bbox[2] + padding_x
            height = text_bbox[3] + padding_y
            canvas.configure(width=width, height=height)
            background_id = self.create_rounded_rectangle(
                canvas,
                0,
                0,
                width,
                height,
                radius,
                fill="#f3f3f3",
                outline="#b8b8b8",
            )
            canvas.tag_lower(background_id, text_id)

            root.update_idletasks()
            screen_width = root.winfo_screenwidth()
            screen_height = root.winfo_screenheight()
            x = max(0, screen_width - width - 24)
            y = max(0, screen_height - height - 96)
            root.geometry(f"+{x}+{y}")
            root.after(3500, root.destroy)
            logging.info("tray popup visible x=%s y=%s width=%s height=%s", x, y, width, height)
            root.mainloop()
            logging.info("run_tray_popup end")
        except Exception:
            logging.exception("run_tray_popup failed")

    def create_rounded_rectangle(
        self,
        canvas,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        radius: int,
        **kwargs,
    ):
        points = [
            x1 + radius,
            y1,
            x2 - radius,
            y1,
            x2,
            y1,
            x2,
            y1 + radius,
            x2,
            y2 - radius,
            x2,
            y2,
            x2 - radius,
            y2,
            x1 + radius,
            y2,
            x1,
            y2,
            x1,
            y2 - radius,
            x1,
            y1 + radius,
            x1,
            y1,
        ]
        return canvas.create_polygon(points, smooth=True, splinesteps=4, **kwargs)


def main() -> int:
    if not acquire_single_instance():
        return 0
    setup_logging()
    logging.info("main start")
    app = DictationApp()
    install_console_ctrl_handler(app)
    try:
        app.run()
    except KeyboardInterrupt:
        logging.info("KeyboardInterrupt received")
        app.exit_app()
    except Exception:
        logging.exception("unhandled exception in main")
        raise
    finally:
        uninstall_console_ctrl_handler()
        if app.restart_requested:
            app.start_replacement_process()
    logging.info("main end")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
