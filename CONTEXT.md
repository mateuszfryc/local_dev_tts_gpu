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

**First model download**:
The required startup step that obtains the first local model before dictation sessions are allowed.
_Avoid_: setup, install

## Example Dialogue

Developer: When does a dictation session start?

Domain expert: It starts when the user presses `Ctrl+Shift+Space` while the tray app is idle.

Developer: Where does the transcript go?

Domain expert: It goes to the focused text input when one exists. Otherwise, the transcript remains in the clipboard.

Developer: When does a model selection take effect?

Domain expert: It applies to the next dictation session after the tray app has returned to idle.

Developer: Can the user start a dictation session before the first model download completes?

Domain expert: No. The hotkey is unavailable until the first model download has succeeded.
