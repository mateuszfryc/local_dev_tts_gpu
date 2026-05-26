import gc
import logging
import shutil
import subprocess
import sys
import threading
import time
from enum import Enum
from pathlib import Path

import pystray

from src.delivery import TranscriptDelivery
from src.hotkeys import HotkeyManager, parse_hotkey
from src.runtime import close_logging_for_local_delete, release_single_instance, setup_logging
from src.settings import (
    CLIPBOARD_MESSAGE,
    DEFAULT_CPU_COMPUTE,
    DEFAULT_GPU_COMPUTE,
    LOCAL_DIR,
    PASTED_MESSAGE,
    SUPPORTED_COMPUTE_TYPES,
    SUPPORTED_DEVICES,
    SUPPORTED_LANGUAGES,
    WORKSPACE_ROOT,
    Config,
    get_available_model_names,
)
from src.speech import AudioRecorder, WhisperEngine
from src.tray_ui import TrayInterface, show_initial_model_dialog, show_shortcut_dialog


class AppState(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    DOWNLOADING_MODEL = "downloading_model"
    WARMING_UP = "warming_up"


class DictationApp:
    def __init__(self) -> None:
        logging.info("DictationApp init start")
        self.config = Config()
        self.model_names = get_available_model_names()
        self.state = AppState.IDLE
        self.state_lock = threading.RLock()
        self.last_toggle_at = 0.0
        self.stop_event = threading.Event()
        self.exit_lock = threading.Lock()
        self.exiting = False
        self.restart_requested = False

        self.tray = TrayInterface()
        self.engine = WhisperEngine(self.notify)
        self.delivery = TranscriptDelivery()
        self.recorder = AudioRecorder(lambda: self.state == AppState.RECORDING)
        self.hotkeys = HotkeyManager(self.toggle_recording, self.stop_event)
        logging.info(
            "DictationApp init complete state=%s language=%s model=%s hotkey=%s device=%s compute_type=%s",
            self.state.value,
            self.config.language,
            self.config.model_name,
            self.config.hotkey,
            self.config.device,
            self.config.compute_type,
        )

    def run(self) -> None:
        logging.info("app run start platform=%s", sys.platform)
        if sys.platform != "win32":
            raise RuntimeError("This application is Windows-only.")

        needs_initial_model = not self.engine.has_downloaded_models()
        logging.info("needs_initial_model=%s", needs_initial_model)
        icon = self.tray.create_icon(self.make_menu())
        logging.info("starting tray icon run loop")
        icon.run(setup=lambda icon: self.finish_startup(icon, needs_initial_model))
        logging.info("tray icon run loop ended")

    def finish_startup(self, icon: pystray.Icon, needs_initial_model: bool) -> None:
        logging.info("finish_startup needs_initial_model=%s", needs_initial_model)
        icon.visible = True
        logging.info("tray icon visible=%s", icon.visible)

        if needs_initial_model:
            logging.info("no downloaded models found; showing initial model dialog")
            initial_model_name = show_initial_model_dialog(self.model_names)
            logging.info("initial model dialog selected model=%s", initial_model_name)
            self.config.model_name = initial_model_name
            self.config.save()
            self.start_download(initial_model_name, "first-model-download")
            return
        self.start_warmup_then_register()

    def make_menu(self) -> pystray.Menu:
        logging.debug("building tray menu")
        return pystray.Menu(
            pystray.MenuItem(
                "toggle recording",
                self.toggle_recording,
                default=True,
                visible=False,
            ),
            pystray.MenuItem("lang", pystray.Menu(*self.make_language_items())),
            pystray.MenuItem("model", pystray.Menu(*self.make_model_items())),
            pystray.MenuItem("device", pystray.Menu(*self.make_device_items())),
            pystray.MenuItem("compute", pystray.Menu(*self.make_compute_items())),
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
            pystray.MenuItem(language, make_action(language), checked=make_checked(language), radio=True)
            for language in SUPPORTED_LANGUAGES
        ]

    def make_model_items(self) -> list[pystray.MenuItem]:
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

    def make_device_items(self) -> list[pystray.MenuItem]:
        def make_action(device: str):
            def action(_icon, _item) -> None:
                logging.info("device menu action selected=%s", device)
                self.set_device(device)

            return action

        def make_checked(device: str):
            def checked(_item) -> bool:
                return self.config.device == device

            return checked

        return [
            pystray.MenuItem(
                device,
                make_action(device),
                checked=make_checked(device),
                enabled=lambda _item: self.state == AppState.IDLE,
                radio=True,
            )
            for device in SUPPORTED_DEVICES
        ]

    def make_compute_items(self) -> list[pystray.MenuItem]:
        def make_action(compute_type: str):
            def action(_icon, _item) -> None:
                logging.info("compute menu action selected=%s", compute_type)
                self.set_compute_type(compute_type)

            return action

        def make_checked(compute_type: str):
            def checked(_item) -> bool:
                return self.config.compute_type == compute_type

            return checked

        return [
            pystray.MenuItem(
                compute_type,
                make_action(compute_type),
                checked=make_checked(compute_type),
                enabled=lambda _item: self.state == AppState.IDLE,
                radio=True,
            )
            for compute_type in SUPPORTED_COMPUTE_TYPES
        ]

    def notify(self, message: str) -> None:
        self.tray.notify(message)

    def refresh_icon(self) -> None:
        self.tray.refresh(active=self.state != AppState.IDLE)

    def register_hotkey(self) -> None:
        registered_hotkey = self.hotkeys.register(self.config.hotkey)
        if registered_hotkey and registered_hotkey != self.config.hotkey:
            self.config.hotkey = registered_hotkey
            self.config.save()

    def unregister_hotkey(self) -> None:
        self.hotkeys.unregister()

    def set_language(self, language: str) -> None:
        logging.info("set_language requested language=%s", language)
        if language not in SUPPORTED_LANGUAGES:
            logging.warning("ignored unsupported language=%s", language)
            return
        self.config.language = language
        self.config.save()
        if self.tray.icon:
            self.tray.icon.update_menu()
        logging.info("language updated to %s", language)

    def rewarm_current_model(self) -> None:
        if self.tray.icon:
            self.tray.icon.update_menu()
        if not self.engine.has_downloaded_models():
            return
        if self.hotkeys.registered:
            self.unregister_hotkey()
        self.start_warmup_then_register()

    def set_model(self, model_name: str) -> None:
        logging.info("set_model requested model_name=%s", model_name)
        if model_name not in self.model_names:
            logging.warning("ignored unsupported model_name=%s", model_name)
            return
        with self.state_lock:
            logging.info("set_model current state=%s", self.state.value)
            if self.state != AppState.IDLE:
                self.notify("Cannot change model while recording or transcribing.")
                return
            should_download_first_model = not self.engine.has_downloaded_models()
            if self.config.model_name == model_name and not should_download_first_model:
                logging.info("set_model ignored: model already selected")
                return
            self.config.model_name = model_name
            self.config.save()
            self.engine.reset()
        if should_download_first_model:
            if self.tray.icon:
                self.tray.icon.update_menu()
            self.start_download(model_name, "model-menu-download")
            return

        self.rewarm_current_model()

    def set_device(self, device: str) -> None:
        logging.info("set_device requested device=%s", device)
        if device not in SUPPORTED_DEVICES:
            logging.warning("ignored unsupported device=%s", device)
            return
        with self.state_lock:
            if self.state != AppState.IDLE:
                self.notify("Cannot change device while recording or transcribing.")
                return
            if self.config.device == device:
                logging.info("set_device ignored: device already selected")
                return
            self.config.device = device
            self.config.compute_type = DEFAULT_GPU_COMPUTE if device == "gpu" else DEFAULT_CPU_COMPUTE
            self.config.save()
            self.engine.reset()
        self.rewarm_current_model()

    def set_compute_type(self, compute_type: str) -> None:
        logging.info("set_compute_type requested compute_type=%s", compute_type)
        if compute_type not in SUPPORTED_COMPUTE_TYPES:
            logging.warning("ignored unsupported compute_type=%s", compute_type)
            return
        with self.state_lock:
            if self.state != AppState.IDLE:
                self.notify("Cannot change compute type while recording or transcribing.")
                return
            if self.config.compute_type == compute_type:
                logging.info("set_compute_type ignored: compute_type already selected")
                return
            self.config.compute_type = compute_type
            self.config.save()
            self.engine.reset()
        self.rewarm_current_model()

    def set_shortcut(self, _icon=None, _item=None) -> None:
        logging.info("set_shortcut requested")
        with self.state_lock:
            if self.state != AppState.IDLE:
                self.notify("Cannot change shortcut while recording or transcribing.")
                return

        was_registered = self.hotkeys.registered
        if was_registered:
            self.unregister_hotkey()

        selected_hotkey = show_shortcut_dialog(self.config.hotkey)
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

        self.config.hotkey = parsed_hotkey.text
        self.config.save()
        if self.tray.icon:
            self.tray.icon.update_menu()

        if was_registered:
            self.register_hotkey()
            if not self.hotkeys.registered:
                self.notify(f"shortcut registration failed: {parsed_hotkey.text}")
                return
        self.notify(f"shortcut set to {parsed_hotkey.text}")

    def start_download(self, model_name: str, thread_name: str) -> None:
        threading.Thread(
            target=self.download_selected_model,
            args=(model_name,),
            name=thread_name,
            daemon=True,
        ).start()

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
            self.engine.download_model(model_name)
        except Exception as exc:
            logging.exception("download failed model_name=%s", model_name)
            self.notify(f"error downloading model {model_name}: {exc}")
            with self.state_lock:
                self.state = AppState.IDLE
                self.refresh_icon()
            return

        logging.info("download completed model_name=%s", model_name)
        self.config.model_name = model_name
        self.config.save()
        self.notify(f"downloaded model {model_name}")
        self.start_warmup_then_register()

    def start_warmup_then_register(self) -> None:
        logging.info("start_warmup_then_register requested")
        threading.Thread(target=self.warmup_then_register, name="model-warmup", daemon=True).start()

    def warmup_then_register(self) -> None:
        logging.info("warmup_then_register start")
        warmup_succeeded = False
        with self.state_lock:
            if self.state not in {AppState.IDLE, AppState.DOWNLOADING_MODEL}:
                logging.info("warmup skipped because state=%s", self.state.value)
                return
            self.state = AppState.WARMING_UP
            self.refresh_icon()

        try:
            warmup_succeeded = self.engine.warmup(
                self.config.language,
                self.config.model_name,
                self.config.device,
                self.config.compute_type,
            )
        except Exception as exc:
            logging.exception("model warmup failed")
            self.notify(f"model warmup failed: {exc}")
        finally:
            with self.state_lock:
                self.state = AppState.IDLE
                self.refresh_icon()
            if warmup_succeeded and not self.exiting:
                self.register_hotkey()

    def toggle_recording(self) -> None:
        logging.info("toggle_recording invoked")
        with self.state_lock:
            if not self.hotkeys.registered or not self.engine.has_downloaded_models():
                logging.info(
                    "toggle ignored hotkey_registered=%s has_downloaded_models=%s",
                    self.hotkeys.registered,
                    self.engine.has_downloaded_models(),
                )
                return

            now = time.monotonic()
            if now - self.last_toggle_at < 0.6:
                logging.info("toggle ignored by debounce elapsed=%.3f", now - self.last_toggle_at)
                return
            self.last_toggle_at = now

            if self.state == AppState.IDLE:
                self.delivery.capture_target_window()
                self.recorder.start()
                self.state = AppState.RECORDING
                self.refresh_icon()
                self.notify("recording started")
                return
            if self.state == AppState.RECORDING:
                audio = self.recorder.stop()
                self.state = AppState.TRANSCRIBING
                self.refresh_icon()
                self.notify("transcription started")
                threading.Thread(
                    target=self.transcribe_and_deliver,
                    args=(audio,),
                    name="transcription",
                    daemon=True,
                ).start()

    def transcribe_and_deliver(self, audio) -> None:
        logging.info("transcribe_and_deliver start audio_shape=%s", getattr(audio, "shape", None))
        try:
            if audio.size == 0:
                self.notify("No audio recorded.")
                return

            text = self.engine.transcribe_audio(
                audio,
                self.config.language,
                self.config.model_name,
                self.config.device,
                self.config.compute_type,
            )
            if not text:
                self.notify("No speech detected.")
                return

            delivered_to_input = self.delivery.deliver_text(text)
            logging.info("deliver_text result delivered_to_input=%s", delivered_to_input)
            self.notify(PASTED_MESSAGE if delivered_to_input else CLIPBOARD_MESSAGE)
        except Exception as exc:
            logging.exception("transcribe_and_deliver failed")
            self.notify(f"Transcription failed: {exc}")
        finally:
            with self.state_lock:
                self.state = AppState.IDLE
                self.refresh_icon()

    def remove_local_data(self, _icon=None, _item=None) -> None:
        logging.info("remove_local_data requested")
        with self.state_lock:
            if self.state != AppState.IDLE:
                self.notify("Cannot remove local data while recording or transcribing.")
                return
            self.state = AppState.DOWNLOADING_MODEL
            self.refresh_icon()

        self.unregister_hotkey()
        self.recorder.stop_stream()
        self.engine.reset()
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
        if self.tray.icon:
            self.tray.icon.update_menu()

        with self.state_lock:
            self.state = AppState.IDLE
            self.refresh_icon()

        model_name = show_initial_model_dialog(self.model_names)
        self.config.model_name = model_name
        self.config.save()
        self.start_download(model_name, "debug-local-data-download")

    def open_logs_directory(self, _icon=None, _item=None) -> None:
        self.tray.open_logs_directory(self.notify)

    def restart_app(self, _icon=None, _item=None) -> None:
        logging.info("restart_app requested")
        self.restart_requested = True
        self.exit_app()

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
                self.recorder.stop_stream()
                self.state = AppState.IDLE
            if self.tray.icon:
                self.tray.icon.stop()
                logging.info("tray icon stop requested")
        except Exception:
            logging.exception("exit_app failed")

    def start_replacement_process(self) -> None:
        logging.info("start_replacement_process requested")
        args = [sys.executable, str(WORKSPACE_ROOT / "main.py")]
        try:
            release_single_instance()
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
        threading.Thread(target=self.exit_app, name="console-exit", daemon=True).start()
