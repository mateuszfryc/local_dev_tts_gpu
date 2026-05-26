import json
import logging
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
LOCAL_DIR = WORKSPACE_ROOT / ".local"
MODELS_DIR = LOCAL_DIR / "models"
SETTINGS_DIR = LOCAL_DIR / "settings"
LOGS_DIR = LOCAL_DIR / "logs"
WARMUP_AUDIO_PATH = WORKSPACE_ROOT / "assets" / "warmup.mp3"

APP_NAME = "Whisper Tray Dictation"
DEFAULT_HOTKEY = "ctrl+shift+space"
DEFAULT_MODEL_NAME = "large-v3"
DEFAULT_DEVICE = "gpu"
DEFAULT_GPU_COMPUTE = "float16"
DEFAULT_CPU_COMPUTE = "int8"
SAMPLE_RATE = 16000
CHANNELS = 1
SUPPORTED_LANGUAGES = ("auto", "en", "pl")
SUPPORTED_DEVICES = ("gpu", "cpu")
SUPPORTED_COMPUTE_TYPES = ("float16", "int8", "int8_float16", "int8_float32", "float32")
PASTED_MESSAGE = "pasted transcript in active input"
CLIPBOARD_MESSAGE = "transcript  in clipboard"


def get_available_model_names() -> tuple[str, ...]:
    from src.runtime import prepare_runtime_dependencies

    prepare_runtime_dependencies()
    from faster_whisper.utils import available_models

    logging.info("loading faster-whisper available model names")
    names = tuple(available_models())
    model_names = (DEFAULT_MODEL_NAME, *(name for name in names if name != DEFAULT_MODEL_NAME))
    logging.info("available model names: %s", ", ".join(model_names))
    return model_names


class Config:
    def __init__(self) -> None:
        self.path = SETTINGS_DIR / "config.json"
        self.language = "auto"
        self.model_name = DEFAULT_MODEL_NAME
        self.hotkey = DEFAULT_HOTKEY
        self.device = DEFAULT_DEVICE
        self.compute_type = DEFAULT_GPU_COMPUTE
        logging.info("config initialized with path=%s", self.path)
        self.load()

    def load(self) -> None:
        from src.hotkeys import parse_hotkey

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
            self.hotkey = parsed_hotkey.text
            logging.info("loaded hotkey=%s", self.hotkey)
        elif hotkey is not None:
            logging.warning("ignored unsupported hotkey in config: %s", hotkey)

        device = data.get("device")
        if device in SUPPORTED_DEVICES:
            self.device = device
            logging.info("loaded device=%s", device)
        elif device is not None:
            logging.warning("ignored unsupported device in config: %s", device)

        compute_type = data.get("compute_type")
        if compute_type in SUPPORTED_COMPUTE_TYPES:
            self.compute_type = compute_type
            logging.info("loaded compute_type=%s", compute_type)
        elif compute_type is not None:
            logging.warning("ignored unsupported compute_type in config: %s", compute_type)
        elif self.device == "cpu":
            self.compute_type = DEFAULT_CPU_COMPUTE
            logging.info("defaulted compute_type=%s for cpu device", self.compute_type)

    def save(self) -> None:
        logging.info(
            "saving config path=%s language=%s model_name=%s hotkey=%s device=%s compute_type=%s",
            self.path,
            self.language,
            self.model_name,
            self.hotkey,
            self.device,
            self.compute_type,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "language": self.language,
                    "model_name": self.model_name,
                    "hotkey": self.hotkey,
                    "device": self.device,
                    "compute_type": self.compute_type,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
