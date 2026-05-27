import ctypes
import logging
import os
import sys
import threading
from ctypes import wintypes
from typing import Callable

import pystray
from PIL import Image, ImageDraw

from src.hotkeys import (
    canonical_hotkey_text,
    get_pressed_modifier_names,
    key_name_from_tk_event,
    parse_hotkey,
)
from src.settings import APP_NAME, LOGS_DIR, MODELS_DIR_LABEL


NIM_MODIFY = 0x00000001
NIF_INFO = 0x00000010
NIIF_INFO = 0x00000001


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


class TrayInterface:
    def __init__(self) -> None:
        self.icon: pystray.Icon | None = None
        self.native_balloon_available = True

    def create_icon(self, menu: pystray.Menu) -> pystray.Icon:
        self.icon = pystray.Icon(
            "whisper_tray_dictation",
            self.make_icon(active=False),
            APP_NAME,
            menu,
        )
        return self.icon

    def make_icon(self, active: bool) -> Image.Image:
        logging.debug("creating tray icon image active=%s", active)
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        color = (220, 20, 40, 255) if active else (255, 255, 255, 255)
        outline = (70, 70, 70, 255) if not active else (120, 0, 0, 255)
        draw.ellipse((10, 10, 54, 54), fill=color, outline=outline, width=3)
        return image

    def refresh(self, active: bool) -> None:
        logging.debug("refresh_icon active=%s icon_exists=%s", active, self.icon is not None)
        if not self.icon:
            return
        self.icon.icon = self.make_icon(active=active)
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
            canvas = tk.Canvas(root, bg=transparent_color, highlightthickness=0, borderwidth=0)
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
            background_id = create_rounded_rectangle(
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
            x, y = get_popup_position(root, width, height)
            root.geometry(f"+{x}+{y}")
            apply_rounded_window_region(root, width, height, radius)
            root.after(3500, root.destroy)
            logging.info("tray popup visible x=%s y=%s width=%s height=%s", x, y, width, height)
            root.mainloop()
            logging.info("run_tray_popup end")
        except Exception:
            logging.exception("run_tray_popup failed")

    def open_logs_directory(self, notify: Callable[[str], None]) -> None:
        logging.info("open_logs_directory requested")
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(str(LOGS_DIR))
        except Exception as exc:
            logging.exception("failed to open logs directory")
            notify(f"failed to open logs directory: {exc}")


def create_rounded_rectangle(canvas, x1: int, y1: int, x2: int, y2: int, radius: int, **kwargs):
    points = [
        x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
        x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
        x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=4, **kwargs)


def get_popup_position(root, width: int, height: int) -> tuple[int, int]:
    margin = 12
    if sys.platform == "win32":
        work_area = get_windows_work_area()
        if work_area is not None:
            left, top, right, bottom = work_area
            x = max(left, right - width - margin)
            y = max(top, bottom - height - margin)
            logging.info(
                "tray popup positioned from work area left=%s top=%s right=%s bottom=%s x=%s y=%s",
                left,
                top,
                right,
                bottom,
                x,
                y,
            )
            return x, y

    x = max(0, root.winfo_screenwidth() - width - margin)
    y = max(0, root.winfo_screenheight() - height - margin)
    logging.info("tray popup positioned from screen fallback x=%s y=%s", x, y)
    return x, y


def get_windows_work_area() -> tuple[int, int, int, int] | None:
    SPI_GETWORKAREA = 0x0030
    rect = wintypes.RECT()
    try:
        if ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETWORKAREA,
            0,
            ctypes.byref(rect),
            0,
        ):
            return rect.left, rect.top, rect.right, rect.bottom
        logging.warning("SPI_GETWORKAREA failed: %s", ctypes.WinError())
    except Exception:
        logging.exception("failed to query Windows work area")
    return None


def apply_rounded_window_region(root, width: int, height: int, radius: int) -> None:
    if sys.platform != "win32":
        return
    try:
        root.update_idletasks()
        hwnd = root.winfo_id()
        diameter = max(1, radius * 2)
        region = ctypes.windll.gdi32.CreateRoundRectRgn(
            0,
            0,
            width + 1,
            height + 1,
            diameter,
            diameter,
        )
        if not region:
            logging.warning("tray popup rounded region creation failed: %s", ctypes.WinError())
            return
        if not ctypes.windll.user32.SetWindowRgn(hwnd, region, True):
            ctypes.windll.gdi32.DeleteObject(region)
            logging.warning("tray popup rounded region apply failed: %s", ctypes.WinError())
            return
        logging.info("tray popup rounded region applied radius=%s", radius)
    except Exception:
        logging.exception("tray popup rounded region failed")


def show_initial_model_dialog(model_names: tuple[str, ...]) -> str:
    logging.info("show_initial_model_dialog start")
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title(APP_NAME)
    root.resizable(False, False)
    root.attributes("-topmost", True)

    selected_model = tk.StringVar(value=model_names[0])
    chosen_model = {"name": model_names[0]}

    frame = ttk.Frame(root, padding=16)
    frame.grid(row=0, column=0, sticky="nsew")
    message = (
        f"No model is downloaded in {MODELS_DIR_LABEL}.\n"
        "Choose the model to download before dictation can be used."
    )
    ttk.Label(frame, text=message, justify="left").grid(row=0, column=0, sticky="w", pady=(0, 12))
    list_frame = ttk.Frame(frame)
    list_frame.grid(row=1, column=0, sticky="nsew")

    for index, model_name in enumerate(model_names):
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

    ttk.Button(frame, text="OK", command=accept).grid(row=2, column=0, sticky="e", pady=(12, 0))
    root.protocol("WM_DELETE_WINDOW", accept)
    root.bind("<Return>", lambda _event: accept())
    root.update_idletasks()
    x = (root.winfo_screenwidth() - root.winfo_width()) // 2
    y = (root.winfo_screenheight() - root.winfo_height()) // 2
    root.geometry(f"+{x}+{y}")
    root.mainloop()

    logging.info("show_initial_model_dialog end selected=%s", chosen_model["name"])
    return chosen_model["name"]


def show_shortcut_dialog(current_hotkey: str) -> str | None:
    logging.info("show_shortcut_dialog start current_hotkey=%s", current_hotkey)
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("set shortcut")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    captured_hotkey = {"value": current_hotkey}
    result = {"value": None}
    current_text = tk.StringVar(value=current_hotkey)

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
    x = max(0, root.winfo_screenwidth() - root.winfo_width() - 24)
    y = max(0, root.winfo_screenheight() - root.winfo_height() - 96)
    root.geometry(f"+{x}+{y}")
    entry.focus_set()
    root.mainloop()

    logging.info("show_shortcut_dialog end selected=%s", result["value"])
    return result["value"]
