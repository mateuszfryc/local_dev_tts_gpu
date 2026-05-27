# Whisper Tray Dictation

This context describes the user-facing language for a local Windows dictation utility.

## Language

**Dictation session**:
A single cycle that starts with the hotkey beginning microphone recording and ends after the transcript is delivered.
_Avoid_: job, run

**Transcript**:
The text produced from the recorded speech in a dictation session.
_Avoid_: output, result

**Focused text input**:
The text-capable control that currently owns keyboard focus in the active Windows application.
_Avoid_: active input, textbox

**Language mode**:
The saved transcription language preference: `auto`, `en`, or `pl`.
_Avoid_: locale, UI language

**Model selection**:
The saved `faster-whisper` model name used for future dictation sessions.
_Avoid_: engine, backend

**Inference device**:
The saved preference for running transcription on `gpu` or `cpu`. GPU means CUDA device `0`; CPU means no CUDA inference.
_Avoid_: backend, target

**Compute type**:
The saved CTranslate2 inference precision/quantization setting, such as `float16`, `int8`, or `int8_float16`.
_Avoid_: dtype, precision

**First model download**:
The required startup step that obtains the first local model before dictation sessions are allowed.
_Avoid_: setup, install

**Model warmup**:
The startup step that transcribes `assets/warmup.mp3` and discards the transcript so the selected model and GPU dependencies are loaded before the first dictation session.
_Avoid_: preload, dummy run

**Runtime local data**:
The saved settings, downloaded models, and runtime logs used by the current app installation.
_Avoid_: workspace data, build data

**Start with Windows**:
The tray menu option that controls whether the current app installation starts automatically when the current Windows user signs in.
_Avoid_: autostart, startup task

**Tray notification**:
A short message shown near the Windows tray area after important app events, including transcript delivery, model download, and model warmup.
_Avoid_: toast, alert

**Local data reset**:
The debug action that removes runtime local data, clears saved settings and downloaded models, and returns the app to first model download.
_Avoid_: cleanup, wipe

## Example Dialogue

Developer: When does a dictation session start?

Domain expert: It starts when the user presses `Ctrl+Shift+Space` while the tray app is idle.

Developer: Where does the transcript go?

Domain expert: It goes to the focused text input when one exists. Otherwise, the transcript remains in the clipboard.

Developer: When does a model selection take effect?

Domain expert: It applies to the next dictation session after the tray app has returned to idle.

Developer: Can the user start a dictation session before the first model download completes?

Domain expert: No. The hotkey is unavailable until the first model download has succeeded.
