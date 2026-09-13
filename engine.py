"""Local speech -> text -> Vietnamese. No network calls or audio/transcript storage."""
from __future__ import annotations

import configure_paths
import os
# Set before importing inference libraries: never download during capture or file tests.
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

from dataclasses import dataclass
from collections import deque
from math import gcd
from pathlib import Path
import json
import queue
import re
import threading
import time
import numpy as np
from scipy.signal import resample_poly
from models import ASR_DIR, MT_DIR, missing_models

SAMPLE_RATE = 16000


def mono_resample(samples: np.ndarray, rate: int) -> np.ndarray:
    """Accept float audio, preserve duration, and antialias before downsampling."""
    audio = np.asarray(samples, dtype=np.float32)
    if audio.ndim == 2:
        audio = audio.mean(axis=1)
    if rate != SAMPLE_RATE:
        divisor = gcd(rate, SAMPLE_RATE)
        audio = resample_poly(audio, SAMPLE_RATE // divisor, rate // divisor)
    return np.ascontiguousarray(audio, dtype=np.float32)


class PhraseBuffer:
    """RMS gate with pre-roll, a silence boundary, and a hard latency/memory bound."""
    def __init__(self, rate: int, max_seconds: float = 5.0, threshold: float = .003):
        self.rate, self.max_seconds, self.threshold = rate, max_seconds, threshold
        self.parts = []
        self.pre = deque(maxlen=2)  # 200 ms when fed 100 ms capture blocks
        self.samples = 0
        self.silence = 0

    def feed(self, block: np.ndarray):
        voiced = float(np.sqrt(np.mean(block * block))) >= self.threshold
        if not self.parts:
            if not voiced:
                self.pre.append(block.copy())
                return None
            self.parts.extend(self.pre)
            self.samples = sum(len(p) for p in self.pre)
            self.pre.clear()
        self.parts.append(block.copy())
        self.samples += len(block)
        self.silence = 0 if voiced else self.silence + len(block)
        if self.samples >= self.rate * self.max_seconds or self.silence >= self.rate * .6:
            return self.flush()
        return None

    def flush(self):
        if not self.parts:
            return None
        audio = np.concatenate(self.parts)
        self.parts.clear()
        self.samples = self.silence = 0
        return audio


def put_latest(q: queue.Queue, item) -> bool:
    """Bound lag: discard oldest unprocessed audio, and tell the UI when that happens."""
    dropped = False
    while True:
        try:
            q.put_nowait(item)
            return dropped
        except queue.Full:
            try:
                q.get_nowait()
                dropped = True
            except queue.Empty:
                pass


@dataclass(frozen=True)
class Caption:
    english: str
    vietnamese: str
    processing_seconds: float
    age_seconds: float = 0


class LocalTranslator:
    def __init__(self):
        missing = missing_models()
        if missing:
            raise FileNotFoundError("Chưa có mô hình. Chạy Setup.cmd trước.\n" + "\n".join(missing))
        import ctranslate2
        import sentencepiece as spm
        from faster_whisper import WhisperModel
        self.asr = WhisperModel(str(ASR_DIR), device="cpu", compute_type="int8",
                                cpu_threads=4, local_files_only=True)
        self.mt = ctranslate2.Translator(str(MT_DIR / "model"), device="cpu",
                                        compute_type="int8", intra_threads=4)
        self.tokenizer = spm.SentencePieceProcessor(model_file=str(MT_DIR / "sentencepiece.model"))
        metadata = json.loads((MT_DIR / "metadata.json").read_text(encoding="utf-8"))
        self.prefix = metadata.get("target_prefix", "")

    def translate(self, text: str) -> str:
        # Audio chunks are short; split only on sentence punctuation, without another neural model.
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
        if not sentences:
            return ""
        tokens = [self.tokenizer.encode(s, out_type=str) for s in sentences]
        results = self.mt.translate_batch(tokens, beam_size=2, replace_unknowns=True,
            length_penalty=.2, max_decoding_length=256,
            target_prefix=[[self.prefix]] * len(tokens) if self.prefix else None)
        translated = []
        for result in results:
            value = self.tokenizer.decode(result.hypotheses[0]).replace("▁", " ").replace("_", " ").strip()
            if self.prefix and value.startswith(self.prefix):
                value = value[len(self.prefix):].strip()
            translated.append(value)
        return " ".join(translated)

    def process(self, audio: np.ndarray, stopped: threading.Event | None = None) -> Caption | None:
        started = time.monotonic()
        segments, _ = self.asr.transcribe(audio, language="en", beam_size=1,
            vad_filter=True, vad_parameters={"min_silence_duration_ms": 400},
            condition_on_previous_text=False, temperature=0)
        text_parts = []
        for segment in segments:
            if stopped and stopped.is_set():
                return None
            if segment.no_speech_prob < .6 and segment.avg_logprob > -1.0:
                text_parts.append(segment.text.strip())
        text = " ".join(text_parts).strip()
        if not text or (stopped and stopped.is_set()):
            return None
        translated = self.translate(text)
        if stopped and stopped.is_set():
            return None
        return Caption(text, translated, time.monotonic() - started)


def list_devices():
    """Enumerate output loopbacks only. Never start any microphone/capture stream."""
    import pyaudiowpatch as pa
    with pa.PyAudio() as p:
        devices = list(p.get_loopback_device_info_generator())
        try:
            default = p.get_default_wasapi_loopback()["index"]
        except (OSError, LookupError):
            default = None
    return devices, default


class LiveSession:
    def __init__(self, translator, device_index: int, seconds: float, stopped, emit):
        self.translator, self.device_index, self.seconds = translator, device_index, seconds
        self.stopped, self.emit = stopped, emit

    def run(self):
        import pyaudiowpatch as pa
        pending = queue.Queue(maxsize=2)
        callback_errors = queue.Queue(maxsize=1)
        with pa.PyAudio() as p:
            device = p.get_device_info_by_index(self.device_index)
            if not device.get("isLoopbackDevice"):
                raise ValueError("Nguồn âm thanh phải là đầu ra WASAPI loopback.")
            rate, channels = int(device["defaultSampleRate"]), int(device["maxInputChannels"])
            buffer = PhraseBuffer(rate, self.seconds)
            last_level = 0.0

            def callback(data, frame_count, time_info, flags):
                nonlocal last_level
                if self.stopped.is_set():
                    return (None, pa.paComplete)
                try:
                    if flags:
                        self.emit("warning", "Thiết bị bị tràn bộ đệm âm thanh; có thể mất một đoạn.")
                    audio = np.frombuffer(data, dtype=np.int16).astype(np.float32).reshape(-1, channels)
                    mono = audio.mean(axis=1) / 32768.0
                    now = time.monotonic()
                    if now - last_level >= .2:
                        self.emit("level", min(1., float(np.sqrt(np.mean(mono * mono))) * 8))
                        last_level = now
                    phrase = buffer.feed(mono)
                    if phrase is not None:
                        if put_latest(pending, (phrase, now)):
                            self.emit("warning", "Máy xử lý chậm: đã bỏ đoạn cũ để theo kịp âm thanh.")
                    return (None, pa.paContinue)
                except Exception as error:
                    put_latest(callback_errors, error)
                    return (None, pa.paAbort)

            if self.stopped.is_set():
                return
            stream = p.open(format=pa.paInt16, channels=channels, rate=rate, input=True,
                input_device_index=self.device_index, frames_per_buffer=rate // 10,
                stream_callback=callback, start=False)
            try:
                if self.stopped.is_set():
                    return
                stream.start_stream()
                self.emit("listening", device["name"])
                while not self.stopped.is_set():
                    if not callback_errors.empty():
                        raise callback_errors.get_nowait()
                    if not stream.is_active():
                        raise RuntimeError("Luồng âm thanh đã ngắt. Kiểm tra tai nghe/loa rồi chọn lại nguồn.")
                    try:
                        audio, captured_at = pending.get(timeout=.15)
                    except queue.Empty:
                        continue
                    result = self.translator.process(mono_resample(audio, rate), self.stopped)
                    if result and not self.stopped.is_set():
                        self.emit("caption", Caption(result.english, result.vietnamese,
                            result.processing_seconds, time.monotonic() - captured_at))
            finally:
                # A stop event makes the next callback return paComplete, even while inference runs.
                stream.close()
                while not pending.empty():
                    pending.get_nowait()


def process_file(translator, path: str, seconds: float, stopped, emit):
    """Read a chosen sample file in small blocks; does not open a capture device."""
    import av
    from av.audio.resampler import AudioResampler
    buffer = PhraseBuffer(SAMPLE_RATE, seconds)
    resampler = AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    carry = np.empty(0, dtype=np.float32)

    def consume(audio):
        nonlocal carry
        carry = np.concatenate((carry, audio))
        while len(carry) >= 1600 and not stopped.is_set():
            chunk, carry = carry[:1600], carry[1600:]
            phrase = buffer.feed(chunk)
            if phrase is not None:
                result = translator.process(phrase, stopped)
                if result and not stopped.is_set():
                    emit("caption", result)

    with av.open(str(Path(path))) as container:
        for frame in container.decode(audio=0):
            if stopped.is_set():
                return
            for resampled in resampler.resample(frame):
                consume(resampled.to_ndarray().flatten().astype(np.float32) / 32768.)
        for resampled in resampler.resample(None):
            consume(resampled.to_ndarray().flatten().astype(np.float32) / 32768.)
    if len(carry) and not stopped.is_set():
        phrase = buffer.feed(carry)
        if phrase is not None:
            result = translator.process(phrase, stopped)
            if result and not stopped.is_set():
                emit("caption", result)
    phrase = buffer.flush()
    if phrase is not None and not stopped.is_set():
        result = translator.process(phrase, stopped)
        if result and not stopped.is_set():
            emit("caption", result)
