"""Own-window UI verification. Real file inference; synthetic lifecycle, no capture."""
import configure_paths
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import socket
import struct
import time
import tkinter as tk
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "test-results"


def save_window(window, path):
    """Ask Windows to render only this app's own HWND; no desktop screenshot."""
    window.update_idletasks()
    user, gdi = ctypes.windll.user32, ctypes.windll.gdi32
    user.GetParent.argtypes = [wintypes.HWND]
    user.GetParent.restype = wintypes.HWND
    user.GetWindowDC.argtypes = [wintypes.HWND]
    user.GetWindowDC.restype = wintypes.HDC
    gdi.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi.CreateCompatibleDC.restype = wintypes.HDC
    gdi.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi.SelectObject.restype = wintypes.HGDIOBJ
    gdi.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    user.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    gdi.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi.DeleteDC.argtypes = [wintypes.HDC]
    user.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    hwnd = user.GetParent(window.winfo_id())
    rect = wintypes.RECT()
    user.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    dc = user.GetWindowDC(hwnd)
    mem = gdi.CreateCompatibleDC(dc)
    bitmap = gdi.CreateCompatibleBitmap(dc, width, height)
    old = gdi.SelectObject(mem, bitmap)
    try:
        if not user.PrintWindow(hwnd, mem, 2):
            raise RuntimeError("PrintWindow failed")
        gdi.SelectObject(mem, old)
        info = ctypes.create_string_buffer(struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 32, 0, 0, 0, 0, 0, 0))
        pixels = ctypes.create_string_buffer(width * height * 4)
        if not gdi.GetDIBits(mem, bitmap, 0, height, pixels, info, 0):
            raise RuntimeError("GetDIBits failed")
        import numpy as np
        bgra = np.frombuffer(pixels.raw, dtype=np.uint8).reshape(height, width, 4)
        rgb = bgra[:, :, [2, 1, 0]]
        raw = b"".join(b"\x00" + row.tobytes() for row in rgb)
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        path.write_bytes(png + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    finally:
        gdi.DeleteObject(bitmap)
        gdi.DeleteDC(mem)
        user.ReleaseDC(hwnd, dc)


def main():
    def no_network(*a, **kw):
        raise AssertionError("Unexpected network connection")
    socket.socket.connect = no_network
    socket.create_connection = no_network
    from app import App
    root = tk.Tk()
    app = App(root)
    result = {"real_capture_opened": False}
    errors = []
    def tk_error(exc_type, value, tb):
        errors.append(str(value))
    root.report_callback_exception = tk_error

    def pump_until(condition, seconds=60):
        deadline = time.monotonic() + seconds
        while not condition():
            root.update()
            if errors:
                raise AssertionError(errors)
            if time.monotonic() > deadline:
                raise TimeoutError("UI smoke timed out")
            time.sleep(.02)

    try:
        root.update()
        assert app.overlay.attributes("-topmost")
        assert not app.active
        assert app.ready
        assert app.sample.winfo_ismapped()
        assert app.sample.winfo_rooty() + app.sample.winfo_height() <= root.winfo_rooty() + root.winfo_height()
        app.begin(path=str(OUT / "english-sample.wav"))
        pump_until(lambda: not app.active)
        assert not app.has_error, app.detail.get()
        content = app.history.get("1.0", "end")
        assert "Xin chào mọi người" in content, content
        assert "thiết bị âm thanh" in content, content
        assert app.start.cget("state") == "normal"
        result["real_file_translation_visible"] = True
        save_window(root, OUT / "controls.png")
        save_window(app.overlay, OUT / "subtitles.png")
        app.show_english.set(False)
        app.toggle_english()
        assert not app.overlay.en.winfo_manager()
        app.show_english.set(True)
        app.toggle_english()
        app.font_size.set(30)
        app.change_font()
        app.overlay.show_caption("Kiểm tra phụ đề dài: " + "đây là một câu tiếng Việt có dấu. " * 8,
                                 "Long subtitle layout test.")
        root.update()
        assert app.overlay.vi.winfo_height() >= app.overlay.vi.winfo_reqheight()
        result["font_bilingual_long_caption_layout"] = True

        class SyntheticSession:
            def __init__(self, translator, device, seconds, stopped, emit):
                self.stopped, self.emit = stopped, emit
            def run(self):
                self.emit("listening", "Synthetic lifecycle test; no device opened")
                self.stopped.wait(5)
                from engine import Caption
                self.emit("caption", Caption("STALE", "STALE", 0))

        with patch("engine.LiveSession", SyntheticSession):
            app.clear()
            app.start_live()
            pump_until(lambda: "Đang nghe" in app.status.get())
            app.stop()
            pump_until(lambda: not app.active)
            assert "STALE" not in app.history.get("1.0", "end")
            assert app.stop_button.cget("state") == "disabled"
            app.start_live()
            pump_until(lambda: "Đang nghe" in app.status.get())
            app.close()
            pump_until(lambda: not app.active)
        result["start_stop_restart_close_and_stale_caption_suppression"] = True
        assert not errors, errors
        (OUT / "ui-check.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print("PASS", json.dumps(result))
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


if __name__ == "__main__":
    main()
