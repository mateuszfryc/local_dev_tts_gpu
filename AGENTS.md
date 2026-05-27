# Agent Notes

Use `README.md` as the user-facing project overview and `CONTEXT.md` as the domain glossary.

## Runtime Behavior Reference

- The app is intentionally Windows-only.
- By default `Ctrl+Shift+Space` toggles recording on and off, and the shortcut can be changed via the tray menu.
- Left-clicking the tray icon toggles recording the same way the default shortcut does.
- The first press or click starts recording from the default microphone.
- The second press or click stops recording and transcribes the audio with the selected `faster-whisper` model.
- If the runtime model directory has no downloaded model, startup asks which model to download before dictation is enabled.
- The startup model dialog allows one model selection and has a single `OK` button.
- While the first model downloads, the app shows tray notifications: `downloading model <name>`, `downloaded model <name>`, or an error message.
- The global hotkey is not registered until the first model download and model warmup succeed.
- The transcript is pasted into the focused text input when one is detected.
- If no focused text input is detected, the transcript is left in the clipboard.
- The tray icon is white when idle and red while recording, transcribing, downloading, or warming up the model.
- Right-click the tray icon to restart, exit, choose transcription language, choose the model, choose inference device/compute settings, set the recording shortcut, enable or disable starting with Windows, or use debug actions.
- The `set shortcut` tray option opens a small input above the tray area; press the shortcut combination and then Enter to save it.
- The `start with windows` tray option adds or removes the current app command from the current user's Windows startup registry entries.
- The language menu supports `auto`, `en`, and `pl`.
- The model menu is populated from `faster_whisper.utils.available_models()`, with `large-v3` placed first as the default.
- The current language and model are shown with native tray menu radio/check indicators.
- The selected language, model, shortcut, inference device, and compute type are saved in `.local/settings/config.json` during development or `data/settings/config.json` in a packaged build.
- Models are downloaded and cached under `.local/models/` during development or `data/models/` in a packaged build.
- The debug menu can remove the runtime local data directory and restart model selection, or open its `logs/` directory.
- GPU inference uses CUDA device `0` by default with `float16` compute. CPU inference uses `int8` by default.
- If GPU is selected but unavailable, the app shows `gpu inference not available, running on cpu` and falls back to CPU `int8`.
