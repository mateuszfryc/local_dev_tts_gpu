import ctypes
import importlib.util
import logging
import os
import sys
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

from src.settings import LOGS_DIR


ERROR_ALREADY_EXISTS = 183
CTRL_C_EVENT = 0
CTRL_BREAK_EVENT = 1
CTRL_CLOSE_EVENT = 2
SINGLE_INSTANCE_MUTEX_NAME = "Local\\WhisperTrayDictation"

SUPPORTED_PYTHON_MIN = (3, 9)
SUPPORTED_PYTHON_MAX = (3, 11)

LOG_FILE: Path | None = None
SINGLE_INSTANCE_MUTEX_HANDLE = None
CONSOLE_CTRL_HANDLER = None
_DLL_DIRECTORIES = []
_DLL_HANDLES = []
_DLL_DIRECTORIES_ADDED = set()
_DLLS_PRELOADED = False


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


def release_single_instance() -> None:
    global SINGLE_INSTANCE_MUTEX_HANDLE
    if sys.platform != "win32" or SINGLE_INSTANCE_MUTEX_HANDLE is None:
        return
    try:
        ctypes.windll.kernel32.CloseHandle(SINGLE_INSTANCE_MUTEX_HANDLE)
        logging.info("single-instance mutex released")
    except Exception:
        logging.exception("failed to release single-instance mutex")
    finally:
        SINGLE_INSTANCE_MUTEX_HANDLE = None


def install_console_ctrl_handler(app) -> None:
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
    for noisy_logger in ("PIL", "httpcore", "httpx", "huggingface_hub", "urllib3"):
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

    venv_nvidia_root = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
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

    for package_name in ("ctranslate2", "onnxruntime"):
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
    if directory_text in _DLL_DIRECTORIES_ADDED:
        return
    _DLL_DIRECTORIES.append(os.add_dll_directory(str(directory)))
    _DLL_DIRECTORIES_ADDED.add(directory_text)


def add_path_directory(directory: Path) -> None:
    current_path = os.environ.get("PATH", "")
    directory_text = str(directory)
    path_parts = [part for part in current_path.split(os.pathsep) if part]
    if any(part.lower() == directory_text.lower() for part in path_parts):
        return
    os.environ["PATH"] = directory_text + os.pathsep + current_path
    logging.debug("prepended PATH directory: %s", directory)


def preload_nvidia_dlls() -> None:
    global _DLLS_PRELOADED
    if sys.platform != "win32":
        return
    if _DLLS_PRELOADED:
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
    search_roots = [Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"]
    spec = importlib.util.find_spec("nvidia")
    if spec is not None and spec.submodule_search_locations is not None:
        search_roots.extend(Path(path) for path in spec.submodule_search_locations)

    for dll_name in dll_names:
        dll_path = next(
            (path for root in search_roots for path in root.rglob(dll_name) if path.exists()),
            None,
        )
        if dll_path is None:
            logging.warning("NVIDIA DLL not found for preload: %s", dll_name)
            continue
        try:
            _DLL_HANDLES.append(ctypes.WinDLL(str(dll_path)))
            logging.info("preloaded NVIDIA DLL: %s", dll_path)
        except Exception:
            logging.exception("failed to preload NVIDIA DLL: %s", dll_path)
    _DLLS_PRELOADED = True


def prepare_runtime_dependencies() -> None:
    add_nvidia_dll_directories()
    add_package_dll_directories()
    preload_nvidia_dlls()
