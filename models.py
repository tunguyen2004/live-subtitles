"""Explicit, download-only model setup. Runtime never uses this module to fetch data."""
import configure_paths  # configure caches before importing download libraries
from pathlib import Path
import hashlib
import json
import shutil
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
MODEL_DIR = ROOT / "models"
ASR_DIR = MODEL_DIR / "whisper-base.en"
MT_DIR = MODEL_DIR / "translate-en_vi-1_9"
ARGOS_URL = "https://argos-net.com/v1/translate-en_vi-1_9.argosmodel"
WHISPER_REPO = "Systran/faster-whisper-base.en"
WHISPER_REVISION = "3d3d5dee26484f91867d81cb899cfcf72b96be6c"
ARGOS_SHA256 = "86957101aa4099aa9a1a7492e41987d938d3cf0fdaf4fb684c0797a9d567dd16"


def missing_models():
    required = [ASR_DIR / "model.bin", ASR_DIR / "config.json",
                ASR_DIR / "tokenizer.json",
                MT_DIR / "model/model.bin", MT_DIR / "sentencepiece.model",
                MT_DIR / "metadata.json"]
    return [str(p.relative_to(ROOT)) for p in required if not p.is_file()]


def install():
    from huggingface_hub import snapshot_download
    MODEL_DIR.mkdir(exist_ok=True)
    print("1/2 Downloading English speech model (~145 MB)...", flush=True)
    snapshot_download(WHISPER_REPO, revision=WHISPER_REVISION, local_dir=ASR_DIR,
                      allow_patterns=["model.bin", "config.json", "tokenizer.json", "vocabulary.*"])
    print("2/2 Downloading English -> Vietnamese model (~68 MB)...", flush=True)
    if any(not (MT_DIR / p).is_file() for p in ("model/model.bin", "sentencepiece.model", "metadata.json")):
        archive = MODEL_DIR / "translation.argosmodel.part"
        request = urllib.request.Request(ARGOS_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=60) as response, archive.open("wb") as out:
            shutil.copyfileobj(response, out)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if digest != ARGOS_SHA256:
            raise ValueError("Model download checksum mismatch. Rerun setup; do not use this archive.")
        # Only extract regular members under our model directory; never execute package code.
        with zipfile.ZipFile(archive) as z:
            for member in z.infolist():
                dest = (MODEL_DIR / member.filename).resolve()
                if not dest.is_relative_to(MODEL_DIR.resolve()):
                    raise ValueError("Unsafe path in model archive")
            z.extractall(MODEL_DIR)
        (MODEL_DIR / "translation-download.json").write_text(
            json.dumps({"url": ARGOS_URL, "sha256": digest}, indent=2), encoding="utf-8")
        archive.unlink()
    missing = missing_models()
    if missing:
        raise RuntimeError("Missing model files: " + ", ".join(missing))
    print("Ready. Models are local. Run Start.cmd.", flush=True)


if __name__ == "__main__":
    install()
