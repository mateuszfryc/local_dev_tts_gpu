# Whisper Tray Dictation For Windows 10/11

Windows-only tray dictation app using `faster-whisper`.

## What You Can Do

- Dictate text into the currently focused Windows text input.
- Start and stop recording with `Ctrl+Shift+Space` or by left-clicking the tray icon.
- Change the recording shortcut from the tray menu.
- Choose the transcription language: `auto`, `en`, or `pl`.
- Choose the `faster-whisper` model used for transcription.
- Choose GPU or CPU inference and adjust the compute type.
- Let the app paste the transcript into the focused text input, or keep it in the clipboard when no text input is focused.
- Restart or exit the app from the tray menu.
- Enable or disable starting with Windows from the tray menu.
- Open runtime logs from the tray menu.
- Reset runtime local data when you want to clear saved settings and downloaded models.
- Build a portable Windows app directory with `DevSTT_[version].exe` and matching `data/` folder.

## Development setup

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

### Run

```powershell
.\.venv\Scripts\Activate.ps1
python .\main.py
```

The first transcription can take longer because `faster-whisper` downloads the selected model into `.local/models/`.

If no model exists in `.local/models/`, the first run shows a model picker before the tray app accepts `Ctrl+Shift+Space`.

Before the app accepts the hotkey, it transcribes `assets/warmup.mp3` and discards the result. This loads the selected model and GPU dependencies ahead of the first real dictation.

## Build

`VERSION` stores the manually maintained SemVer application version used in the executable name.

`build.ps1` reads `VERSION`, creates `dist/<timestamp>/`, runs the PyInstaller onedir build, and creates an empty `data/` directory next to the executable.

`DevSTT.spec` contains the PyInstaller packaging definition for application code, runtime assets, and native package dependencies.

Version numbers use `MAJOR.MINOR.PATCH`.

- `1.x.x` is the main program version, referenced as `1`.
- `x.1.x` is a complete new functionality that did not exist before, such as a new setting, a new option selection window, or new model support.
- `x.x.1` is a fix or improvement to existing functionality.

## GPU notes

`faster-whisper` uses CTranslate2. GPU mode requires a compatible NVIDIA GPU and an installed NVIDIA display driver.

`requirements.txt` includes NVIDIA CUDA 12/cuDNN 9 pip wheels:

- `nvidia-cuda-runtime-cu12`
- `nvidia-cublas-cu12`
- `nvidia-cudnn-cu12==9.*`

On Windows, the script adds the DLL directories from those wheels before importing `faster-whisper`, so a separate CUDA Toolkit/cuDNN install should not be required for the usual case. If CUDA device `0` still cannot be used, the script falls back to CPU with int8 inference.

## Local files

- `.local/models/` stores downloaded model files.
- `.local/settings/config.json` stores the selected language, model, shortcut, inference device, and compute type.
- `.local/logs/[timestamp].log` stores one runtime log file per program session.
- In a packaged build, `data/` next to `DevSTT_[version].exe` has the same structure as `.local/`, so `.local/*` can be copied into `data/` manually.

## Code layout

- `main.py` is the thin entrypoint.
- `src/runtime.py` owns process startup, logging, Python version checks, Ctrl+C handling, and CUDA/NVIDIA DLL preparation.
- `src/settings.py` owns local paths, saved settings, language modes, model selection, and available model discovery.
- `src/hotkeys.py` owns global shortcut parsing and Win32 hotkey registration.
- `src/speech.py` owns microphone recording, model download, model warmup, and transcription.
- `src/startup.py` owns the current-user Windows startup registry entry.
- `src/delivery.py` owns focused text input detection and transcript paste/clipboard delivery.
- `src/tray_ui.py` owns the tray icon, menus, dialogs, and visible notifications.
- `src/app.py` coordinates the dictation session state machine across those modules.

## Operational notes

- The app is intentionally Windows-only.
- The hotkey is a toggle, not a hold-to-record shortcut.
- To paste into another application, that application must have a focused text-capable control at the moment transcription finishes.
- The app uses the clipboard as the transport for paste, so a successfully pasted transcript may also remain in the clipboard.
