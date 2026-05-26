import logging
import os
import tempfile
import wave
from pathlib import Path
from typing import Callable

import numpy as np
import sounddevice as sd

from src.runtime import prepare_runtime_dependencies
from src.settings import CHANNELS, MODELS_DIR, SAMPLE_RATE, WARMUP_AUDIO_PATH


class AudioRecorder:
    def __init__(self, recording_guard: Callable[[], bool]) -> None:
        self.recording_guard = recording_guard
        self.frames: list[np.ndarray] = []
        self.stream: sd.InputStream | None = None

    def start(self) -> None:
        logging.info("start_recording")
        self.frames = []
        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            callback=self._audio_callback,
        )
        self.stream.start()
        logging.info("recording stream started sample_rate=%s channels=%s", SAMPLE_RATE, CHANNELS)

    def _audio_callback(self, indata, _frames, _time_info, status) -> None:
        if status:
            logging.warning("audio callback status=%s", status)
        if self.recording_guard():
            self.frames.append(indata.copy())

    def stop(self) -> np.ndarray:
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


class WhisperEngine:
    def __init__(self, notify: Callable[[str], None]) -> None:
        self.notify = notify
        self.model = None
        self.model_device = ""
        self.model_key: tuple[str, str, str] | None = None
        self.gpu_disabled = False

    def reset(self) -> None:
        self.model = None
        self.model_device = ""
        self.model_key = None
        self.gpu_disabled = False

    def has_downloaded_models(self) -> bool:
        logging.debug("checking downloaded models in %s", MODELS_DIR)
        if not MODELS_DIR.exists():
            logging.debug("models dir does not exist")
            return False
        result = any(MODELS_DIR.rglob("model.bin"))
        logging.debug("has_downloaded_models=%s", result)
        return result

    def download_model(self, model_name: str) -> None:
        prepare_runtime_dependencies()
        from faster_whisper.utils import download_model as download_faster_whisper_model

        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        logging.info("models dir ensured path=%s", MODELS_DIR)
        download_faster_whisper_model(model_name, cache_dir=str(MODELS_DIR))

    def transcribe_audio(
        self,
        audio: np.ndarray,
        language_mode: str,
        model_name: str,
        device: str,
        compute_type: str,
    ) -> str:
        if audio.size == 0:
            return ""

        wav_path = self.write_temp_wav(audio)
        logging.info("temporary wav created path=%s", wav_path)
        try:
            return self.transcribe_file(wav_path, language_mode, model_name, device, compute_type)
        finally:
            try:
                os.remove(wav_path)
                logging.info("temporary wav removed path=%s", wav_path)
            except OSError:
                logging.exception("failed to remove temporary wav path=%s", wav_path)

    def warmup(self, language_mode: str, model_name: str, device: str, compute_type: str) -> bool:
        if not WARMUP_AUDIO_PATH.exists():
            logging.warning("warmup audio missing: %s", WARMUP_AUDIO_PATH)
            self.notify("warmup audio missing")
            return False

        self.notify(f"warming up model {model_name}")
        text = self.transcribe_file(
            str(WARMUP_AUDIO_PATH),
            language_mode,
            model_name,
            device,
            compute_type,
        )
        logging.info("warmup transcription discarded text_length=%s", len(text))
        self.notify(f"model ready {model_name}")
        return True

    def transcribe_file(
        self,
        wav_path: str,
        language_mode: str,
        model_name: str,
        device: str,
        compute_type: str,
    ) -> str:
        logging.info(
            "transcribe_file path=%s language=%s model=%s device=%s compute_type=%s",
            wav_path,
            language_mode,
            model_name,
            device,
            compute_type,
        )
        language = None if language_mode == "auto" else language_mode
        try:
            text = self._transcribe_with_current_model(
                wav_path,
                language,
                model_name,
                device,
                compute_type,
            )
        except RuntimeError:
            if self.model_device != "cuda":
                raise
            logging.exception("GPU transcription failed; retrying on CPU")
            self.notify("gpu inference not available, running on cpu")
            self.gpu_disabled = True
            self.model = None
            self.model_device = ""
            self.model_key = None
            text = self._transcribe_with_current_model(
                wav_path,
                language,
                model_name,
                "cpu",
                "int8",
            )

        logging.info("transcribe_file complete text_length=%s", len(text))
        return text

    def _transcribe_with_current_model(
        self,
        wav_path: str,
        language: str | None,
        model_name: str,
        device: str,
        compute_type: str,
    ) -> str:
        logging.info(
            "transcribe_with_current_model path=%s language=%s model_device=%s requested_device=%s compute_type=%s gpu_disabled=%s",
            wav_path,
            language or "auto",
            self.model_device or "not_loaded",
            device,
            compute_type,
            self.gpu_disabled,
        )
        model = self.get_model(model_name, device, compute_type)
        segments, _info = model.transcribe(
            wav_path,
            language=language,
            vad_filter=True,
            beam_size=5,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()

    def get_model(self, model_name: str, device: str, compute_type: str):
        requested_key = (model_name, device, compute_type)
        logging.info(
            "get_model requested current_model_loaded=%s requested_key=%s loaded_key=%s",
            self.model is not None,
            requested_key,
            self.model_key,
        )
        if self.model is not None and self.model_key == requested_key:
            return self.model

        prepare_runtime_dependencies()
        from faster_whisper import WhisperModel

        if device == "gpu" and not self.gpu_disabled and self.cuda_device_0_available():
            logging.info("CUDA device 0 available; loading model on GPU")
            try:
                self.model = WhisperModel(
                    model_name,
                    device="cuda",
                    device_index=0,
                    compute_type=compute_type,
                    download_root=str(MODELS_DIR),
                )
                self.model_device = "cuda"
                self.model_key = requested_key
                logging.info(
                    "model loaded on GPU model=%s compute_type=%s",
                    model_name,
                    compute_type,
                )
                return self.model
            except Exception:
                logging.exception("failed to load model on GPU; falling back to CPU")
                self.gpu_disabled = True
                self.notify("gpu inference not available, running on cpu")
        else:
            if device == "cpu":
                logging.info("CPU selected; loading model on CPU")
            elif self.gpu_disabled:
                logging.info("GPU disabled for this session; loading model on CPU")
            else:
                logging.info("CUDA device 0 unavailable; loading model on CPU")
                self.notify("gpu inference not available, running on cpu")

        cpu_compute_type = compute_type if device == "cpu" else "int8"
        logging.info("loading model on CPU model=%s compute_type=%s", model_name, cpu_compute_type)
        self.model = WhisperModel(
            model_name,
            device="cpu",
            compute_type=cpu_compute_type,
            download_root=str(MODELS_DIR),
        )
        self.model_device = "cpu"
        self.model_key = (model_name, "cpu", cpu_compute_type)
        logging.info("model loaded on CPU model=%s compute_type=%s", model_name, cpu_compute_type)
        return self.model

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
