"""Keep all application writes, caches and temp files inside the project tree."""
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parent
for key, relative in {
    "TEMP": ".temp", "TMP": ".temp", "TMPDIR": ".temp",
    "HF_HOME": ".cache/huggingface", "HF_HUB_CACHE": ".cache/huggingface/hub",
    "HUGGINGFACE_HUB_CACHE": ".cache/huggingface/hub",
    "XDG_CACHE_HOME": ".cache", "TORCH_HOME": ".cache/torch",
}.items():
    path = ROOT / relative
    path.mkdir(parents=True, exist_ok=True)
    os.environ[key] = str(path)
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ["PYTHONNOUSERSITE"] = "1"
