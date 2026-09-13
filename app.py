"""Vietnamese desktop controls and a floating Tk subtitle window.

UI calls stay on the Tk thread. Audio/inference run in a worker; tagged messages
prevent stale captions from a stopped session appearing in a new session.
"""
import configure_paths
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk
from pathlib import Path

from models import missing_models

BG = "#101720"
CARD = "#192330"
TEXT = "#f1f5f9"
MUTED = "#99adbf"
ACCENT = "#58ddbb"


class SubtitleWindow(tk.Toplevel):
    def __init__(self, owner, stop):
        super().__init__(owner)
        self.title("Phụ đề Việt • kéo để di chuyển")
        self.configure(bg="#101720")
        self.attributes("-topmost", True)
        self.attributes("-alpha", .94)
        self.minsize(460, 190)
        width = min(960, self.winfo_screenwidth() - 80)
        self.geometry(f"{width}x240+{(self.winfo_screenwidth()-width)//2}+{max(0,self.winfo_screenheight()-340)}")
        self.protocol("WM_DELETE_WINDOW", self.withdraw)
        bar = tk.Frame(self, bg=CARD, padx=15, pady=7)
        bar.pack(fill="x")
        self.status = tk.Label(bar, text="● ĐÃ DỪNG", fg=MUTED, bg=CARD, font=("Segoe UI", 10, "bold"))
        self.status.pack(side="left")
        tk.Button(bar, text="Dừng nghe", command=stop, bg=CARD, fg=TEXT,
                  relief="flat", padx=10).pack(side="right")
        self.vi = tk.Label(self, text="Phụ đề tiếng Việt sẽ hiện ở đây", fg=TEXT,
                           bg=BG, font=("Segoe UI", 24, "bold"), justify="center")
        self.vi.pack(fill="both", expand=True, padx=22, pady=(12, 5))
        self.en = tk.Label(self, text="Bấm Bắt đầu nghe trong cửa sổ điều khiển.", fg=MUTED,
                          bg=BG, font=("Segoe UI", 12), justify="center")
        self.en.pack(fill="x", padx=22, pady=(0, 18))
        self.bind("<Configure>", self.resize_text)
        self.bind("<Escape>", lambda _: stop())

    def resize_text(self, _=None):
        width = max(380, self.winfo_width() - 44)
        self.vi.configure(wraplength=width)
        self.en.configure(wraplength=width)

    def show_caption(self, vi, en):
        self.vi.configure(text=vi)
        self.en.configure(text=en)
        self.update_idletasks()
        needed = self.vi.winfo_reqheight() + (self.en.winfo_reqheight() if self.en.winfo_manager() else 0) + 95
        if needed > self.winfo_height():
            height = min(needed, self.winfo_screenheight() - 120)
            y = min(self.winfo_y(), self.winfo_screenheight() - height - 60)
            self.geometry(f"{self.winfo_width()}x{height}+{self.winfo_x()}+{max(0,y)}")


class App:
    def __init__(self, root):
        self.root = root
        root.title("Phụ đề Việt — Anh → Việt trên máy của bạn")
        root.configure(bg=BG)
        height = min(820, root.winfo_screenheight() - 100)
        root.geometry(f"820x{height}+80+35")
        root.minsize(720, min(740, height))
        self.events = queue.Queue(maxsize=150)
        self.session_id = 0
        self.active = False
        self.stopped = threading.Event()
        self.worker = None
        self.translator = None
        self.devices = []
        self.last_caption = 0.
        self.warning_until = 0.
        self.closing = False
        self.has_error = False
        self.is_file = False
        self.font_size = tk.IntVar(value=24)
        self.show_english = tk.BooleanVar(value=True)
        self.seconds = tk.StringVar(value="5")
        self.status = tk.StringVar(value="Đã dừng • chưa thu âm")
        self.detail = tk.StringVar(value="Chọn đúng loa hoặc tai nghe mà Meet, Zoom hay video đang phát.")
        self.model_status = tk.StringVar(value="")
        self.setup_style()
        self.build()
        self.overlay = SubtitleWindow(root, self.stop)
        self.check_models()
        self.refresh_devices()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<Escape>", lambda _: self.stop())
        root.after(70, self.poll)

    def setup_style(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TCombobox", fieldbackground="#263647", background="#263647",
                        foreground=TEXT, arrowcolor=ACCENT, padding=7)
        style.map("TCombobox", fieldbackground=[("readonly", "#263647")],
                  foreground=[("readonly", TEXT)])
        style.configure("Meter.Horizontal.TProgressbar", troughcolor="#263647", background=ACCENT)

    def label(self, parent, text, size=11, color=TEXT, bold=False):
        return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color, anchor="w",
                        font=("Segoe UI", size, "bold" if bold else "normal"))

    def button(self, parent, text, command, primary=False):
        return tk.Button(parent, text=text, command=command, bg=ACCENT if primary else "#293b4c",
                         fg=BG if primary else TEXT, activebackground="#8be9d1", activeforeground=BG,
                         disabledforeground="#637483", relief="flat", padx=16, pady=10,
                         cursor="hand2", font=("Segoe UI", 11, "bold"))

    def build(self):
        body = tk.Frame(self.root, bg=BG, padx=26, pady=20)
        body.pack(fill="both", expand=True)
        self.label(body, "NGHE TIẾNG ANH · ĐỌC TIẾNG VIỆT", 10, ACCENT, True).pack(anchor="w")
        self.label(body, "Phụ đề Việt", 29, TEXT, True).pack(anchor="w", pady=(2, 3))
        self.label(body, "Dịch trên máy • Không cần API key • Không lưu âm thanh", 11, MUTED).pack(anchor="w")

        source = tk.Frame(body, bg=CARD, padx=17, pady=14)
        source.pack(fill="x", pady=(20, 12))
        self.label(source, "01   ÂM THANH BẠN ĐANG NGHE", 10, ACCENT, True).pack(anchor="w", pady=(0, 8))
        row = tk.Frame(source, bg=CARD)
        row.pack(fill="x")
        self.device_choice = ttk.Combobox(row, state="readonly", font=("Segoe UI", 10))
        self.device_choice.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.refresh = self.button(row, "Làm mới", self.refresh_devices)
        self.refresh.pack(side="right")
        self.label(source, "Thu toàn bộ âm thanh của đầu ra đã chọn, gồm cả ứng dụng khác.", 10, MUTED).pack(anchor="w", pady=(8, 0))

        settings = tk.Frame(body, bg=BG)
        settings.pack(fill="x", pady=(2, 10))
        self.label(settings, "Đoạn tối đa (giây)", 10, MUTED).pack(side="left")
        self.chunk_choice = ttk.Combobox(settings, textvariable=self.seconds, values=["3", "5", "7"],
                                         state="readonly", width=3)
        self.chunk_choice.pack(side="left", padx=(8, 20))
        self.label(settings, "Cỡ chữ", 10, MUTED).pack(side="left")
        tk.Scale(settings, from_=18, to=34, variable=self.font_size, orient="horizontal",
                 command=self.change_font, bg=BG, fg=TEXT, troughcolor=CARD, highlightthickness=0,
                 length=115).pack(side="left", padx=8)
        tk.Checkbutton(settings, text="Hiện cả tiếng Anh", variable=self.show_english,
                       command=self.toggle_english, bg=BG, fg=TEXT, selectcolor=CARD,
                       activebackground=BG, activeforeground=TEXT, font=("Segoe UI", 10)).pack(side="right")

        actions = tk.Frame(body, bg=BG)
        actions.pack(fill="x", pady=(0, 14))
        self.start = self.button(actions, "▶  Bắt đầu nghe", self.start_live, True)
        self.start.pack(side="left", padx=(0, 8))
        self.stop_button = self.button(actions, "■  Dừng", self.stop)
        self.stop_button.pack(side="left", padx=(0, 8))
        self.stop_button.configure(state="disabled")
        self.button(actions, "Hiện phụ đề", self.show_overlay).pack(side="right")

        state = tk.Frame(body, bg=CARD, padx=17, pady=12)
        state.pack(fill="x")
        tk.Label(state, textvariable=self.status, fg=ACCENT, bg=CARD, anchor="w",
                 font=("Segoe UI", 12, "bold")).pack(fill="x")
        tk.Label(state, textvariable=self.detail, fg=MUTED, bg=CARD, anchor="w", justify="left",
                 wraplength=700, font=("Segoe UI", 10)).pack(fill="x", pady=(5, 8))
        self.meter = ttk.Progressbar(state, style="Meter.Horizontal.TProgressbar", maximum=1.)
        self.meter.pack(fill="x")

        histbar = tk.Frame(body, bg=BG)
        histbar.pack(fill="x", pady=(15, 6))
        self.label(histbar, "PHỤ ĐỀ GẦN ĐÂY", 10, MUTED, True).pack(side="left")
        tk.Button(histbar, text="Xóa", command=self.clear, bg=BG, fg=MUTED, relief="flat").pack(side="right")
        self.history = tk.Text(body, height=6, bg=CARD, fg=TEXT, relief="flat", padx=14, pady=12,
                               wrap="word", font=("Segoe UI", 11), state="disabled")
        self.history.tag_configure("en", foreground=MUTED, font=("Segoe UI", 10))

        footer = tk.Frame(body, bg=BG)
        footer.pack(side="bottom", fill="x", pady=(12, 0))
        self.sample = self.button(footer, "Thử file âm thanh…", self.choose_sample)
        self.sample.pack(side="left")
        tk.Label(footer, textvariable=self.model_status, bg=BG, fg=MUTED, justify="right",
                 font=("Segoe UI", 9)).pack(side="right")
        self.history.pack(fill="both", expand=True)

    def change_font(self, _=None):
        if hasattr(self, "overlay"):
            self.overlay.vi.configure(font=("Segoe UI", self.font_size.get(), "bold"))

    def toggle_english(self):
        if not self.show_english.get():
            self.overlay.en.pack_forget()
        elif not self.overlay.en.winfo_manager():
            self.overlay.en.pack(fill="x", padx=22, pady=(0, 18))

    def show_overlay(self):
        self.overlay.deiconify()
        self.overlay.lift()

    def check_models(self):
        self.ready = not missing_models()
        self.model_status.set("Mô hình ngoại tuyến: sẵn sàng\nCPU · Whisper base.en + Argos en→vi" if self.ready
                              else "Chưa có mô hình\nChạy Setup.cmd để tải lần đầu")
        if not self.active:
            self.start.configure(state="normal" if self.ready and self.devices else "disabled")
            self.sample.configure(state="normal" if self.ready else "disabled")

    def refresh_devices(self):
        if self.active:
            return
        try:
            from engine import list_devices
            self.devices, default = list_devices()
            self.device_choice.configure(values=[d["name"] for d in self.devices])
            if self.devices:
                idx = next((i for i, d in enumerate(self.devices) if d["index"] == default), 0)
                self.device_choice.current(idx)
            else:
                self.device_choice.set("")
                self.detail.set("Không tìm thấy đầu ra WASAPI. Kết nối loa/tai nghe rồi bấm Làm mới.")
        except Exception as error:
            self.devices = []
            self.detail.set(f"Không liệt kê được thiết bị: {error}")
        self.check_models()

    def start_live(self):
        index = self.device_choice.current()
        if index >= 0 and index < len(self.devices):
            self.begin(device=int(self.devices[index]["index"]))

    def choose_sample(self):
        # File input must remain inside the explicitly authorized project tree.
        allowed = Path(__file__).resolve().parent.parent
        path = filedialog.askopenfilename(title=f"Chọn âm thanh tiếng Anh trong {allowed}",
            initialdir=str(Path(__file__).resolve().parent / "test-results"),
            filetypes=[("Âm thanh / video", "*.wav *.mp3 *.m4a *.flac *.ogg *.mp4"), ("Tất cả", "*.*")])
        if path:
            if not Path(path).resolve().is_relative_to(allowed):
                self.detail.set(f"Chỉ đọc file trong {allowed}. Hãy đặt file thử trong thư mục này.")
                return
            self.begin(path=path)

    def begin(self, device=None, path=None):
        if self.active:
            return
        self.check_models()
        if not self.ready:
            return
        self.active, self.has_error, self.is_file = True, False, bool(path)
        self.session_id += 1
        session_id = self.session_id
        self.stopped = threading.Event()
        stopped = self.stopped
        seconds = float(self.seconds.get())
        self.last_caption = 0
        self.status.set("Đang nạp mô hình • chưa thu âm")
        self.detail.set("Lần đầu có thể cần vài giây. Bạn vẫn có thể bấm Dừng.")
        self.overlay.show_caption("Đang chuẩn bị mô hình…", "")
        self.overlay.status.configure(text="● ĐANG CHUẨN BỊ", fg=MUTED)
        self.show_overlay()
        for control in (self.start, self.sample, self.refresh, self.device_choice, self.chunk_choice):
            control.configure(state="disabled")
        self.stop_button.configure(state="normal")

        def emit(kind, value=None):
            # Level/status chatter may be dropped; completion and captions remain reliable.
            if kind in ("level", "warning"):
                try:
                    self.events.put_nowait((session_id, kind, value))
                except queue.Full:
                    pass
            else:
                self.events.put((session_id, kind, value))

        def work():
            try:
                from engine import LocalTranslator, LiveSession, process_file
                if self.translator is None:
                    self.translator = LocalTranslator()
                if stopped.is_set():
                    return
                if path:
                    emit("file", Path(path).name)
                    process_file(self.translator, path, seconds, stopped, emit)
                else:
                    LiveSession(self.translator, device, seconds, stopped, emit).run()
            except Exception as error:
                emit("error", str(error))
            finally:
                emit("done")

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def stop(self):
        if self.active:
            self.stopped.set()
            self.status.set("Đang dừng • chờ tác vụ hiện tại kết thúc")
            self.detail.set("Đã yêu cầu dừng nhận âm thanh; phần đang dịch sẽ bị bỏ qua.")
            self.overlay.status.configure(text="● ĐANG DỪNG", fg=MUTED)
            self.stop_button.configure(state="disabled")
            self.overlay.show_caption("Đã yêu cầu dừng", "")
            self.meter["value"] = 0

    def clear(self):
        self.history.configure(state="normal")
        self.history.delete("1.0", "end")
        self.history.configure(state="disabled")
        self.overlay.show_caption("", "")

    def add_caption(self, caption):
        self.overlay.show_caption(caption.vietnamese, caption.english)
        self.last_caption = time.monotonic()
        self.history.configure(state="normal")
        self.history.insert("end", caption.vietnamese + "\n")
        self.history.insert("end", caption.english + "\n\n", "en")
        lines = int(self.history.index("end-1c").split(".")[0])
        if lines > 150:
            self.history.delete("1.0", f"{lines-150}.0")
        self.history.see("end-3l")
        self.history.configure(state="disabled")
        if time.monotonic() > self.warning_until:
            self.detail.set(f"Xử lý đoạn vừa rồi: {caption.processing_seconds:.1f} giây. "
                            "Độ trễ còn gồm thời gian gom đoạn và chờ xử lý.")

    def poll(self):
        for _ in range(200):
            try:
                session_id, kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if session_id != self.session_id:
                continue
            if kind == "done":
                self.active = False
                if not self.has_error:
                    self.status.set("Đã thử xong file • không thu âm máy" if self.is_file and not self.stopped.is_set()
                                    else "Đã dừng • không thu âm")
                self.overlay.status.configure(text="● ĐÃ DỪNG", fg=MUTED)
                self.stop_button.configure(state="disabled")
                self.refresh.configure(state="normal")
                self.device_choice.configure(state="readonly")
                self.chunk_choice.configure(state="readonly")
                self.meter["value"] = 0
                self.check_models()
                if self.closing:
                    self.root.destroy()
                    return
            elif self.stopped.is_set():
                continue  # includes queued captions after the user pressed Stop
            elif kind == "listening":
                self.status.set("● Đang nghe âm thanh máy tính")
                self.detail.set("Nguồn: " + value)
                self.overlay.status.configure(text="● ĐANG NGHE · EN → VI · TRÊN MÁY", fg=ACCENT)
                self.overlay.show_caption("Đang chờ tiếng Anh…", "")
            elif kind == "file":
                self.status.set("Đang thử file • không thu âm máy")
                self.detail.set(value)
                self.overlay.status.configure(text="● THỬ FILE · KHÔNG THU ÂM MÁY", fg=ACCENT)
            elif kind == "caption":
                self.add_caption(value)
            elif kind == "level":
                self.meter["value"] = value
            elif kind == "warning":
                self.warning_until = time.monotonic() + 8
                self.detail.set(value)
            elif kind == "error":
                self.has_error = True
                self.status.set("Không thể tiếp tục")
                self.detail.set(value)
                self.overlay.show_caption("Đã dừng vì có lỗi", "Xem thông báo trong cửa sổ điều khiển.")
        if self.active and not self.is_file and self.last_caption and time.monotonic() - self.last_caption > 18:
            self.overlay.show_caption("Đang chờ tiếng Anh…", "")
            self.last_caption = 0
        self.root.after(70, self.poll)

    def close(self):
        if self.active:
            self.closing = True
            self.stop()
        else:
            self.root.destroy()


def main():
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
