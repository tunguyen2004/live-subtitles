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

BG = "#0d131c"
CARD = "#151f2c"
FIELD = "#1d2a3a"
LINE = "#2a394b"
TEXT = "#edf3fa"
MUTED = "#a1b2c7"
ACCENT = "#74e0c1"
BLUE = "#a8c5ff"
WARNING = "#f5ca80"
ERROR = "#ffacac"


class SubtitleWindow(tk.Toplevel):
    def __init__(self, owner, stop):
        super().__init__(owner)
        self.title("Phụ đề Việt · Kéo để di chuyển")
        self.configure(bg=BG)
        self.attributes("-topmost", True)
        self.attributes("-alpha", .96)
        scale = max(1., self.winfo_fpixels("1i") / 96)
        self.minimum_height = round(140 * scale)
        self.minsize(round(460 * scale), self.minimum_height)
        self._fit_job = None
        width = min(round(900 * scale), self.winfo_screenwidth() - 80)
        self.geometry(f"{width}x190+{(self.winfo_screenwidth()-width)//2}+{max(0,self.winfo_screenheight()-280)}")
        self.protocol("WM_DELETE_WINDOW", self.withdraw)
        self.rule = tk.Frame(self, bg=LINE, height=2)
        self.rule.pack(fill="x")
        bar = tk.Frame(self, bg=BG, padx=20, pady=5)
        self.bar = bar
        bar.pack(fill="x")
        self.status = tk.Label(bar, text="● Đã dừng", fg=MUTED, bg=BG, font=("Segoe UI", 10))
        self.status.pack(side="left")
        self.stop_button = tk.Button(bar, text="■  Dừng nghe", command=stop, bg=FIELD, fg=TEXT,
            activebackground=LINE, activeforeground=TEXT, disabledforeground=MUTED,
            relief="flat", bd=0, padx=12, pady=5, cursor="hand2", state="disabled",
            font=("Segoe UI", 10), highlightthickness=1, highlightbackground=LINE, highlightcolor=ACCENT)
        self.stop_button.pack(side="right")
        self.vi = tk.Label(self, text="Phụ đề tiếng Việt sẽ hiện ở đây", fg=TEXT,
                           bg=BG, font=("Segoe UI", 24, "bold"), justify="center")
        self.vi.pack(fill="both", expand=True, padx=24, pady=(8, 6))
        self.en = tk.Label(self, text="Bấm Bắt đầu nghe trong cửa sổ điều khiển.", fg=MUTED,
                          bg=BG, font=("Segoe UI", 12), justify="center")
        self.en.pack(fill="x", padx=24, pady=(0, 16))
        self.bind("<Configure>", self.resize_text)
        self.bind("<Escape>", lambda _: stop())

    def resize_text(self, _=None):
        if _ is not None and _.widget is not self:
            return
        width = max(380, self.winfo_width() - 48)
        self.vi.configure(wraplength=width)
        self.en.configure(wraplength=width)
        self.schedule_fit()

    def set_state(self, text, color=MUTED, listening=False):
        self.status.configure(text="● " + text, fg=color)
        self.rule.configure(bg=color if listening else LINE)
        self.stop_button.configure(state="normal" if listening else "disabled")

    def schedule_fit(self):
        if self._fit_job is not None:
            self.after_cancel(self._fit_job)
        self._fit_job = self.after_idle(self.fit_content)

    def fit_content(self):
        self._fit_job = None
        # Grow AND shrink around the content; avoid a large empty overlay after a long sentence.
        needed = self.vi.winfo_reqheight() + (self.en.winfo_reqheight() if self.en.winfo_manager() else 0) + self.bar.winfo_reqheight() + 40
        height = max(self.minimum_height, min(needed, self.winfo_screenheight() - 120))
        if abs(height - self.winfo_height()) > 2:
            y = min(self.winfo_y(), self.winfo_screenheight() - height - 60)
            self.geometry(f"{self.winfo_width()}x{height}+{self.winfo_x()}+{max(0,y)}")

    def show_caption(self, vi, en):
        self.vi.configure(text=vi)
        self.en.configure(text=en)
        self.schedule_fit()


class App:
    def __init__(self, root):
        self.root = root
        root.title("Phụ đề Việt — Anh → Việt trên máy của bạn")
        root.configure(bg=BG)
        self.ui_scale = max(1., root.winfo_fpixels("1i") / 96)
        height = min(round(780 * self.ui_scale), root.winfo_screenheight() - 100)
        width = min(round(1120 * self.ui_scale), root.winfo_screenwidth() - 80)
        root.geometry(f"{width}x{height}+40+35")
        root.minsize(min(round(980 * self.ui_scale), width), min(round(740 * self.ui_scale), height))
        root.option_add("*Font", ("Segoe UI", 10))
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
        self.caption_count = tk.StringVar(value="Chưa có phụ đề")
        self.total_captions = 0
        self.current_caption = None
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
        style.configure("TCombobox", fieldbackground=FIELD, background=FIELD,
                        foreground=TEXT, arrowcolor=MUTED, padding=8, bordercolor=LINE,
                        lightcolor=LINE, darkcolor=LINE, insertcolor=TEXT)
        style.map("TCombobox", fieldbackground=[("readonly", FIELD)],
                  foreground=[("disabled", MUTED), ("readonly", TEXT)],
                  bordercolor=[("focus", ACCENT)])
        self.root.option_add("*TCombobox*Listbox.background", FIELD)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", LINE)
        self.root.option_add("*TCombobox*Listbox.selectForeground", ACCENT)
        style.configure("Meter.Horizontal.TProgressbar", troughcolor=FIELD, background=ACCENT,
                        borderwidth=0, bordercolor=FIELD, lightcolor=FIELD, darkcolor=FIELD, thickness=4)
        style.layout("Meter.Horizontal.TProgressbar", [("Horizontal.Progressbar.trough", {
            "sticky": "nswe", "children": [("Horizontal.Progressbar.pbar", {"side": "left", "sticky": "ns"})]})])
        style.configure("Vertical.TScrollbar", background=LINE, troughcolor=CARD,
                        borderwidth=0, arrowcolor=MUTED, width=10, bordercolor=CARD,
                        lightcolor=LINE, darkcolor=LINE)
        style.map("Vertical.TScrollbar", background=[("disabled", FIELD), ("active", MUTED), ("!disabled", LINE)],
                  lightcolor=[("disabled", FIELD), ("active", MUTED)],
                  darkcolor=[("disabled", FIELD), ("active", MUTED)],
                  arrowcolor=[("disabled", MUTED), ("active", TEXT)])
        style.configure("Horizontal.TScale", background=ACCENT, troughcolor=FIELD, borderwidth=0,
                        bordercolor=FIELD, lightcolor=ACCENT, darkcolor=ACCENT)

    def label(self, parent, text, size=11, color=TEXT, bold=False):
        return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color, anchor="w",
                        font=("Segoe UI", size, "bold" if bold else "normal"))

    def button(self, parent, text, command, primary=False, compact=False):
        base, hover = (ACCENT, "#a0eed7") if primary else (FIELD, LINE)
        button = tk.Button(parent, text=text, command=command, bg=base,
            fg=BG if primary else TEXT, activebackground=hover, activeforeground=BG if primary else TEXT,
            disabledforeground="#8193aa", relief="flat", bd=0, padx=14, pady=7 if compact else 11,
            cursor="hand2", font=("Segoe UI", 10, "bold"), takefocus=True,
            highlightthickness=1, highlightbackground=base, highlightcolor=BLUE)
        button.bind("<Enter>", lambda _: button.configure(bg=hover) if button.cget("state") != "disabled" else None)
        button.bind("<Leave>", lambda _: button.configure(bg=base))
        return button

    def panel(self, parent):
        return tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=LINE)

    def build(self):
        body = tk.Frame(self.root, bg=BG, padx=24, pady=18)
        body.pack(fill="both", expand=True)
        header = tk.Frame(body, bg=BG)
        header.pack(fill="x", pady=(0, 14))
        logo = tk.Canvas(header, width=46, height=46, bg=BG, highlightthickness=0)
        logo.pack(side="left", padx=(0, 12))
        logo.create_rectangle(2, 6, 43, 36, outline=ACCENT, width=2)
        logo.create_line(12, 36, 12, 43, 21, 36, fill=ACCENT, width=2)
        logo.create_line(11, 17, 33, 17, fill=ACCENT, width=2)
        logo.create_line(11, 25, 26, 25, fill=ACCENT, width=2)
        titles = tk.Frame(header, bg=BG)
        titles.pack(side="left")
        self.label(titles, "Phụ đề Việt", 24, TEXT, True).pack(anchor="w")
        self.label(titles, "Nghe trọn câu chuyện, hiểu bằng tiếng Việt.", 10, MUTED).pack(anchor="w")
        route = tk.Frame(header, bg=FIELD, padx=16, pady=10)
        route.pack(side="right", anchor="center")
        self.label(route, "Tiếng Anh  →  Tiếng Việt", 11, BLUE, True).pack()

        state = self.panel(body)
        state.pack(fill="x", pady=(0, 14))
        self.state_rule = tk.Frame(state, bg=LINE, width=3)
        self.state_rule.pack(side="left", fill="y")
        actions = tk.Frame(state, bg=CARD, padx=16, pady=14)
        actions.pack(side="right")
        self.start = self.button(actions, "▶  Bắt đầu nghe", self.start_live, True)
        self.start.pack(side="left", padx=(0, 8))
        self.stop_button = self.button(actions, "■  Dừng", self.stop)
        self.stop_button.pack(side="left")
        self.stop_button.configure(state="disabled")
        state_copy = tk.Frame(state, bg=CARD, padx=16, pady=12)
        state_copy.pack(side="left", fill="both", expand=True)
        self.state_label = tk.Label(state_copy, textvariable=self.status, fg=MUTED, bg=CARD,
            anchor="w", justify="left", font=("Segoe UI", 12, "bold"))
        self.state_label.pack(fill="x")
        state_copy.bind("<Configure>", lambda e: self.state_label.configure(wraplength=max(120, e.width-32)))
        self.detail_text = tk.Text(state_copy, height=2, width=1, bg=CARD, fg=MUTED,
            relief="flat", bd=0, wrap="word", font=("Segoe UI", 10), takefocus=False, cursor="arrow")
        self.detail_text.pack(fill="x", pady=(5, 0))
        self.detail.trace_add("write", self.update_detail)
        self.update_detail()

        footer = tk.Frame(body, bg=BG)
        footer.pack(side="bottom", fill="x", pady=(14, 0))
        tk.Label(footer, textvariable=self.model_status, bg=BG, fg=MUTED,
                 anchor="w", font=("Segoe UI", 9)).pack(side="left")
        self.label(footer, "Esc để dừng khi đang ở ứng dụng", 9, MUTED).pack(side="right")

        columns = tk.Frame(body, bg=BG)
        columns.pack(fill="both", expand=True)
        sidebar_width = round(310 * self.ui_scale)
        columns.columnconfigure(0, weight=0, minsize=sidebar_width)
        columns.columnconfigure(1, weight=1)
        columns.rowconfigure(0, weight=1)
        sidebar = self.panel(columns)
        sidebar.grid(row=0, column=0, sticky="nsew", padx=(0, 18))
        sidebar.configure(width=sidebar_width)
        sidebar.pack_propagate(False)
        source = tk.Frame(sidebar, bg=CARD, padx=16, pady=14)
        source.pack(fill="both", expand=True)
        self.label(source, "Nguồn âm thanh", 12, TEXT, True).pack(anchor="w", pady=(0, 5))
        self.label(source, "Loa hoặc tai nghe đang phát", 10, MUTED).pack(anchor="w", pady=(0, 8))
        row = tk.Frame(source, bg=CARD)
        row.pack(fill="x")
        self.device_choice = ttk.Combobox(row, state="readonly", font=("Segoe UI", 10), width=22)
        self.device_choice.pack(fill="x")
        signal = tk.Frame(source, bg=CARD)
        signal.pack(fill="x", pady=(6, 6))
        self.label(signal, "Mức âm thanh", 9, MUTED).pack(side="left")
        self.refresh = self.button(signal, "Làm mới", self.refresh_devices, compact=True)
        self.refresh.pack(side="right")
        meter_area = tk.Frame(source, bg=FIELD, height=round(4*self.ui_scale))
        meter_area.pack(fill="x", pady=(0, 10))
        meter_area.pack_propagate(False)
        self.meter = ttk.Progressbar(meter_area, style="Meter.Horizontal.TProgressbar", maximum=1.)
        self.meter.pack(fill="both", expand=True)
        note = self.label(source, "Nghe toàn bộ âm thanh của đầu ra này, gồm cả ứng dụng khác.", 9, MUTED)
        note.configure(wraplength=265, justify="left")
        note.pack(fill="x")
        source.bind("<Configure>", lambda e: note.configure(wraplength=max(180, e.width-32)))
        tk.Frame(source, bg=LINE, height=1).pack(fill="x", pady=10)
        self.label(source, "Hiển thị phụ đề", 12, TEXT, True).pack(anchor="w", pady=(0, 8))
        sizes = tk.Frame(source, bg=CARD)
        sizes.pack(fill="x")
        self.label(sizes, "Cỡ chữ", 10, MUTED).pack(side="left")
        self.size_label = self.label(sizes, "24", 10, ACCENT, True)
        self.size_label.pack(side="right")
        self.font_scale = ttk.Scale(source, from_=18, to=34, orient="horizontal", command=self.change_font)
        self.font_scale.set(24)
        self.font_scale.pack(fill="x", pady=(6, 8))
        tk.Checkbutton(source, text="Hiện cả tiếng Anh", variable=self.show_english,
            command=self.toggle_english, bg=CARD, fg=TEXT, selectcolor=FIELD,
            activebackground=CARD, activeforeground=ACCENT, relief="flat", bd=0,
            highlightthickness=1, highlightbackground=CARD, highlightcolor=BLUE,
            font=("Segoe UI", 10), anchor="w").pack(fill="x")
        chunks = tk.Frame(source, bg=CARD)
        chunks.pack(fill="x", pady=(8, 0))
        self.label(chunks, "Đoạn tối đa (giây)", 10, MUTED).pack(side="left")
        self.chunk_choice = ttk.Combobox(chunks, textvariable=self.seconds, values=["3", "5", "7"],
            state="readonly", width=3)
        self.chunk_choice.pack(side="right")
        sample_area = tk.Frame(source, bg=CARD)
        sample_area.pack(side="bottom", fill="x", pady=(12, 0))
        self.sample = self.button(sample_area, "Thử file âm thanh…", self.choose_sample, compact=True)
        self.sample.pack(fill="x")

        reading = tk.Frame(columns, bg=BG)
        reading.grid(row=0, column=1, sticky="nsew")
        reading.rowconfigure(1, weight=1)
        reading.columnconfigure(0, weight=1)
        current = self.panel(reading)
        current.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        current_bar = tk.Frame(current, bg=CARD, padx=18, pady=12)
        current_bar.pack(fill="x")
        self.label(current_bar, "BẢN DỊCH HIỆN TẠI", 9, ACCENT, True).pack(side="left")
        self.overlay_button = self.button(current_bar, "Hiện phụ đề ↗", self.show_overlay, compact=True)
        self.overlay_button.pack(side="right")
        preview_area = tk.Frame(current, bg=CARD)
        preview_area.pack(fill="x", padx=(0, 8), pady=(0, 14))
        preview_scroll = ttk.Scrollbar(preview_area, orient="vertical")
        preview_scroll.pack(side="right", fill="y")
        self.preview = tk.Text(preview_area, height=4, width=1, wrap="word", bg=CARD, fg=TEXT,
            relief="flat", bd=0, padx=18, pady=6, font=("Segoe UI", 19, "bold"), cursor="arrow")
        self.preview.pack(side="left", fill="x", expand=True)
        self.preview.configure(yscrollcommand=preview_scroll.set)
        preview_scroll.configure(command=self.preview.yview)
        self.preview.tag_configure("vi", foreground=TEXT, font=("Segoe UI", 19, "bold"), spacing3=10)
        self.preview.tag_configure("en", foreground=MUTED, font=("Segoe UI", 11))
        self.preview.tag_configure("placeholder", foreground=MUTED, font=("Segoe UI", 15))
        self.render_preview()

        history_panel = self.panel(reading)
        history_panel.grid(row=1, column=0, sticky="nsew")
        histbar = tk.Frame(history_panel, bg=CARD, padx=18, pady=10)
        histbar.pack(fill="x")
        self.label(histbar, "Phụ đề gần đây", 12, TEXT, True).pack(side="left")
        self.button(histbar, "Xóa", self.clear, compact=True).pack(side="right")
        tk.Label(history_panel, textvariable=self.caption_count, bg=CARD, fg=MUTED,
            anchor="w", font=("Segoe UI", 9)).pack(fill="x", padx=18, pady=(0, 8))
        transcript = tk.Frame(history_panel, bg=CARD)
        transcript.pack(fill="both", expand=True, padx=(8, 8), pady=(0, 12))
        scroll = ttk.Scrollbar(transcript, orient="vertical")
        scroll.pack(side="right", fill="y")
        self.history = tk.Text(transcript, height=3, width=1, bg=CARD, fg=TEXT, relief="flat", bd=0,
            padx=10, pady=4, wrap="word", font=("Segoe UI", 11), state="disabled",
            yscrollcommand=scroll.set, selectbackground=LINE, selectforeground=TEXT)
        self.history.pack(side="left", fill="both", expand=True)
        scroll.configure(command=self.history.yview)
        self.history.tag_configure("vi", font=("Segoe UI", 11, "bold"), spacing1=4, spacing3=3)
        self.history.tag_configure("en", foreground=MUTED, font=("Segoe UI", 10), spacing3=6)
        self.history_empty = tk.Label(self.history, text="Những câu vừa dịch sẽ xuất hiện ở đây.\nLịch sử chỉ giữ trong cửa sổ này.",
            bg=CARD, fg=MUTED, font=("Segoe UI", 10), justify="center")
        self.history_empty.place(relx=.5, rely=.5, anchor="center")

    def update_detail(self, *_):
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        self.detail_text.insert("1.0", self.detail.get())
        self.detail_text.configure(state="disabled")

    def set_status(self, text, color=MUTED):
        self.status.set(text)
        self.state_label.configure(fg=color)
        self.state_rule.configure(bg=color if self.active or self.has_error else LINE)

    def render_preview(self):
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        if self.current_caption:
            self.preview.insert("end", self.current_caption.vietnamese + "\n", "vi")
            if self.show_english.get():
                self.preview.insert("end", self.current_caption.english, "en")
        else:
            heading = "Đang chờ bản dịch…" if self.active else "Sẵn sàng lắng nghe."
            hint = "Phụ đề sẽ xuất hiện sau khi xử lý đoạn tiếng Anh." if self.active else "Bắt đầu nghe hoặc thử một file tiếng Anh để xem bản dịch."
            if self.has_error:
                heading, hint = "Tạm dừng dịch.", "Xem thông báo phía trên, kiểm tra nguồn âm thanh rồi thử lại."
            elif not getattr(self, "ready", True):
                heading, hint = "Chưa có mô hình.", "Chạy Setup.cmd để chuẩn bị bộ nhận dạng và dịch ngoại tuyến."
            self.preview.insert("end", heading + "\n", "vi")
            self.preview.insert("end", hint, "placeholder")
        self.preview.configure(state="disabled")

    def change_font(self, _=None):
        if _ is not None:
            self.font_size.set(round(float(_)))
        if hasattr(self, "size_label"):
            self.size_label.configure(text=str(self.font_size.get()))
        if hasattr(self, "overlay"):
            self.overlay.vi.configure(font=("Segoe UI", self.font_size.get(), "bold"))
            self.overlay.schedule_fit()

    def toggle_english(self):
        if not self.show_english.get():
            self.overlay.en.pack_forget()
        elif not self.overlay.en.winfo_manager():
            self.overlay.en.pack(fill="x", padx=24, pady=(0, 16))
        self.overlay.schedule_fit()
        self.render_preview()

    def show_overlay(self):
        self.overlay.deiconify()
        self.overlay.lift()

    def check_models(self):
        self.ready = not missing_models()
        self.model_status.set("●  Ngoại tuyến sẵn sàng  ·  Không lưu âm thanh" if self.ready
                              else "●  Chưa có mô hình  ·  Chạy Setup.cmd")
        if not self.current_caption:
            self.render_preview()
        if not self.active:
            self.start.configure(state="normal" if self.ready and self.devices else "disabled")
            self.sample.configure(state="normal" if self.ready else "disabled")

    def refresh_devices(self):
        if self.active:
            return
        try:
            from engine import list_devices
            self.devices, default = list_devices()
            self.device_choice.configure(values=[d["name"].replace(" [Loopback]", "") for d in self.devices])
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
        self.current_caption = None
        self.render_preview()
        self.session_id += 1
        session_id = self.session_id
        self.stopped = threading.Event()
        stopped = self.stopped
        seconds = float(self.seconds.get())
        self.last_caption = 0
        self.set_status("Đang chuẩn bị · chưa thu âm", BLUE)
        self.detail.set("Lần đầu có thể cần vài giây. Bạn vẫn có thể bấm Dừng.")
        self.overlay.show_caption("Đang chuẩn bị mô hình…", "")
        self.overlay.set_state("Đang chuẩn bị", BLUE, listening=True)
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
            self.set_status("Đang dừng…", WARNING)
            self.detail.set("Đã yêu cầu dừng nhận âm thanh; phần đang dịch sẽ bị bỏ qua.")
            self.overlay.set_state("Đang dừng…", WARNING)
            self.stop_button.configure(state="disabled")
            self.overlay.show_caption("Đã yêu cầu dừng", "")
            self.meter["value"] = 0

    def clear(self):
        self.history.configure(state="normal")
        self.history.delete("1.0", "end")
        self.history.configure(state="disabled")
        self.overlay.show_caption("", "")
        self.total_captions = 0
        self.caption_count.set("Chưa có phụ đề")
        self.history_empty.place(relx=.5, rely=.5, anchor="center")
        self.current_caption = None
        self.render_preview()

    def add_caption(self, caption):
        self.current_caption = caption
        self.render_preview()
        self.total_captions += 1
        self.caption_count.set(f"{self.total_captions} đoạn đã dịch · Chỉ giữ trong cửa sổ này")
        self.history_empty.place_forget()
        self.overlay.show_caption(caption.vietnamese, caption.english)
        self.last_caption = time.monotonic()
        self.history.configure(state="normal")
        self.history.insert("end", caption.vietnamese + "\n", "vi")
        self.history.insert("end", caption.english + "\n", "en")
        lines = int(self.history.index("end-1c").split(".")[0])
        if lines > 150:
            self.history.delete("1.0", f"{lines-150}.0")
        self.history.see("end-1c")
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
                    self.set_status("Đã thử xong file · không thu âm máy" if self.is_file and not self.stopped.is_set()
                                    else "Đã dừng · không thu âm")
                self.overlay.set_state("Đã dừng" if not self.has_error else "Đã dừng vì lỗi",
                                       MUTED if not self.has_error else ERROR)
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
                self.set_status("● Đang nghe âm thanh máy tính", ACCENT)
                self.detail.set("Nguồn: " + value)
                self.overlay.set_state("Đang nghe · Anh → Việt", ACCENT, listening=True)
                self.overlay.show_caption("Đang chờ tiếng Anh…", "")
            elif kind == "file":
                self.set_status("Đang thử file · không thu âm máy", BLUE)
                self.detail.set(value)
                self.overlay.set_state("Đang thử file · không thu âm", BLUE, listening=True)
            elif kind == "caption":
                self.add_caption(value)
            elif kind == "level":
                self.meter["value"] = value
            elif kind == "warning":
                self.warning_until = time.monotonic() + 8
                self.detail.set(value)
                self.state_rule.configure(bg=WARNING)
            elif kind == "error":
                self.has_error = True
                self.set_status("Đã dừng vì có lỗi", ERROR)
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


def configure_window_scaling():
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


def main():
    configure_window_scaling()
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
