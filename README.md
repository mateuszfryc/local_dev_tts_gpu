# Whisper Tray Dictation For Windows 10/11

Windows-only tray dictation app using `faster-whisper`.

## Behavior

- Starts as a Windows tray application.
- `Ctrl+Shift+Space` toggles recording by default.
- The first press starts recording from the default microphone.
- The second press stops recording and transcribes the audio with the selected `faster-whisper` model.
- If `.local/models/` has no downloaded model, startup asks which model to download before dictation is enabled.
- The startup model dialog allows one model selection and has a single `OK` button.
- While the first model downloads, the app shows tray notifications: `downloading model <name>`, `downloaded model <name>`, or an error message.
- The global hotkey is not registered until the first model download and model warmup succeed.
- The transcript is pasted into the focused text input when one is detected.
- If no focused text input is detected, the transcript is left in the clipboard.
- The tray icon is white when idle and red while recording, transcribing, downloading, or warming up the model.
- Right-click the tray icon to restart, exit, choose transcription language, choose the model, set the recording shortcut, or use debug actions.
- The `set shortcut` tray option opens a small input above the tray area; press the shortcut combination and then Enter to save it.
- The language menu supports `auto`, `en`, and `pl`.
- The model menu is populated from `faster_whisper.utils.available_models()`, with `large-v3` placed first as the default.
- The current language and model are shown with native tray menu radio/check indicators.
- The selected language, model, and shortcut are saved in `.local/settings/config.json`.
- Models are downloaded and cached under `.local/models/` in this workspace.
- The debug menu can remove `.local/` and restart model selection, or open `.local/logs/`.
- GPU inference uses CUDA device `0` by default. If it is unavailable, the app shows `gpu inference not available, running on cpu` and falls back to CPU.

## Create a local environment

Use Python `3.11`. `faster-whisper` declares `python_requires=">=3.9"`, but its official package classifiers currently list support up to Python `3.11`, so this project targets Python `3.11` instead of the locally installed Python `3.14.0`.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If `py -3.11` is not available, install Python `3.11` for Windows first. The fallback below only works if your default `python` is already a supported Python version:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Run

```powershell
.\.venv\Scripts\Activate.ps1
python .\tray_whisper_dictation.py
```

The first transcription can take longer because `faster-whisper` downloads the selected model into `.local/models/`.

If no model exists in `.local/models/`, the first run shows a model picker before the tray app accepts `Ctrl+Shift+Space`.

Before the app accepts the hotkey, it transcribes `assets/warmup.mp3` and discards the result. This loads the selected model and GPU dependencies ahead of the first real dictation.

## GPU notes

`faster-whisper` uses CTranslate2. GPU mode requires a compatible NVIDIA GPU and an installed NVIDIA display driver.

`requirements.txt` includes NVIDIA CUDA 12/cuDNN 9 pip wheels:

- `nvidia-cuda-runtime-cu12`
- `nvidia-cublas-cu12`
- `nvidia-cudnn-cu12==9.*`

On Windows, the script adds the DLL directories from those wheels before importing `faster-whisper`, so a separate CUDA Toolkit/cuDNN install should not be required for the usual case. If CUDA device `0` still cannot be used, the script falls back to CPU with int8 inference.

## Local files

- `.local/models/` stores downloaded model files.
- `.local/settings/config.json` stores the selected language, model, and shortcut.
- `.local/logs/[timestamp].log` stores one runtime log file per program session.

## Operational notes

- The app is intentionally Windows-only.
- The hotkey is a toggle, not a hold-to-record shortcut.
- To paste into another application, that application must have a focused text-capable control at the moment transcription finishes.
- The app uses the clipboard as the transport for paste, so a successfully pasted transcript may also remain in the clipboard.
