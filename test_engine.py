import configure_paths
import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock, patch
import numpy as np
from engine import PhraseBuffer, mono_resample, put_latest, LocalTranslator, LiveSession


class AudioTests(unittest.TestCase):
    def test_silence_never_builds_unbounded_buffer(self):
        b = PhraseBuffer(16000)
        for _ in range(1000):
            self.assertIsNone(b.feed(np.zeros(1600, dtype=np.float32)))
        self.assertEqual(len(b.parts), 0)
        self.assertEqual(len(b.pre), 2)

    def test_phrase_has_preroll_and_closes_after_silence(self):
        b = PhraseBuffer(16000)
        for _ in range(3):
            b.feed(np.zeros(1600, dtype=np.float32))
        for _ in range(10):
            self.assertIsNone(b.feed(np.full(1600, .1, dtype=np.float32)))
        for _ in range(5):
            self.assertIsNone(b.feed(np.zeros(1600, dtype=np.float32)))
        phrase = b.feed(np.zeros(1600, dtype=np.float32))
        self.assertEqual(len(phrase), 1600 * 18)
        self.assertIsNone(b.flush())

    def test_continuous_audio_is_split_with_no_missing_samples(self):
        b = PhraseBuffer(16000, 3)
        original = np.full(16000 * 8, .1, dtype=np.float32)
        chunks = []
        for i in range(0, len(original), 1600):
            out = b.feed(original[i:i+1600])
            if out is not None:
                chunks.append(out)
        chunks.append(b.flush())
        self.assertEqual([len(x) for x in chunks], [48000, 48000, 32000])
        np.testing.assert_array_equal(np.concatenate(chunks), original)

    def test_stereo_44100_resample_preserves_duration_and_tone(self):
        t = np.arange(44100) / 44100
        tone = np.sin(2 * np.pi * 440 * t).astype(np.float32)
        out = mono_resample(np.column_stack((tone, tone)), 44100)
        self.assertEqual(len(out), 16000)
        self.assertEqual(out.dtype, np.float32)
        self.assertEqual(np.argmax(abs(np.fft.rfft(out))), 440)

    def test_backpressure_keeps_newest_audio(self):
        q = queue.Queue(maxsize=2)
        self.assertFalse(put_latest(q, 1))
        self.assertFalse(put_latest(q, 2))
        self.assertTrue(put_latest(q, 3))
        self.assertEqual([q.get_nowait(), q.get_nowait()], [2, 3])

    def test_stop_during_recognition_prevents_translation(self):
        engine = LocalTranslator.__new__(LocalTranslator)
        stop = threading.Event()
        def segments():
            stop.set()
            yield SimpleNamespace(text="Hello", no_speech_prob=.1, avg_logprob=0)
        engine.asr = Mock()
        engine.asr.transcribe.return_value = (segments(), None)
        engine.translate = Mock()
        self.assertIsNone(engine.process(np.ones(16000), stop))
        engine.translate.assert_not_called()

    def test_no_speech_is_not_translated(self):
        engine = LocalTranslator.__new__(LocalTranslator)
        engine.asr = Mock()
        engine.asr.transcribe.return_value = ([SimpleNamespace(text="Thank you", no_speech_prob=.95, avg_logprob=0)], None)
        engine.translate = Mock()
        self.assertIsNone(engine.process(np.zeros(16000)))
        engine.translate.assert_not_called()


class CaptureTests(unittest.TestCase):
    def test_callback_stops_capture_during_long_inference_and_closes_stream(self):
        # Fake the device boundary. This test never opens real system audio.
        stopped = threading.Event()
        notices = []
        fake = MagicMock()
        fake.paInt16, fake.paContinue, fake.paComplete, fake.paAbort = 8, 0, 1, 2
        port = Mock()
        fake.PyAudio.return_value.__enter__.return_value = port
        port.get_device_info_by_index.return_value = {
            "isLoopbackDevice": True, "defaultSampleRate": 16000,
            "maxInputChannels": 2, "name": "Synthetic loopback"}
        stream = Mock()
        port.open.return_value = stream
        stream.is_active.return_value = True
        callback = None
        def open_stream(**kw):
            nonlocal callback
            callback = kw["stream_callback"]
            return stream
        port.open.side_effect = open_stream
        data = np.full((1600, 2), 5000, dtype=np.int16).tobytes()
        def start_stream():
            for _ in range(30):
                self.assertEqual(callback(data, 1600, {}, 0)[1], fake.paContinue)
        stream.start_stream.side_effect = start_stream
        translator = Mock()
        def process(audio, event):
            event.set()
            self.assertEqual(callback(data, 1600, {}, 0)[1], fake.paComplete)
            return SimpleNamespace(english="stale", vietnamese="cũ", processing_seconds=1)
        translator.process.side_effect = process
        with patch.dict("sys.modules", {"pyaudiowpatch": fake}):
            LiveSession(translator, 17, 3, stopped, lambda k, v: notices.append(k)).run()
        stream.close.assert_called_once()
        self.assertNotIn("caption", notices)

    def test_microphone_is_rejected(self):
        fake = MagicMock()
        port = fake.PyAudio.return_value.__enter__.return_value
        port.get_device_info_by_index.return_value = {"isLoopbackDevice": False}
        with patch.dict("sys.modules", {"pyaudiowpatch": fake}):
            with self.assertRaises(ValueError):
                LiveSession(Mock(), 1, 3, threading.Event(), Mock()).run()
        port.open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
