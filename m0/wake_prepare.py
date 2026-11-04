"""Download everything the "Hey Pip" detector training needs (run inside the wake-train venv).

    ~/pip-data/m0/wake-train/.venv/bin/python wake_prepare.py

Writes progress to ~/pip-data/m0/tasks/wake-prepare.json for the dashboard.
"""
import json
import os
import time
import urllib.request
from pathlib import Path

import numpy as np

W = Path.home() / "pip-data/m0/wake-train"
TASK = Path.home() / "pip-data/m0/tasks/wake-prepare.json"
OWW_MODELS = W / "openwakeword/openwakeword/resources/models"
REL = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"
HF = "https://huggingface.co/datasets/davidscripka/openwakeword_features/resolve/main/"
ACAV_BYTES = 4 * 1024**3  # keep ~4 GB of the 17 GB negative-feature library
FMA_HOURS = 2


def progress(stage, done=0, total=0, note="", finished=False, failed=False):
    TASK.parent.mkdir(parents=True, exist_ok=True)
    obj = {"name": "Wake-word training: downloads", "stage": stage, "done": done, "total": total, "note": note,
           "finished": finished, "failed": failed, "updated": time.time()}
    tmp = TASK.with_suffix(".tmp"); tmp.write_text(json.dumps(obj)); os.replace(tmp, TASK)
    print(f"[{time.strftime('%H:%M:%S')}] {stage} {done}/{total} {note}", flush=True)


def fetch(url, dest: Path, limit=None, stage="Downloading"):
    if dest.exists() and (limit is None or dest.stat().st_size >= limit):
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "pip-m0"})
    if limit:
        req.add_header("Range", f"bytes=0-{limit - 1}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as fh:
        total = limit or int(r.headers.get("Content-Length") or 0)
        got, last = 0, 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk); got += len(chunk)
            if time.time() - last > 5:
                progress(stage, got // (1 << 20), total // (1 << 20), f"{dest.name} (MB)"); last = time.time()
            if limit and got >= limit:
                break
    os.replace(tmp, dest)


def truncate_npy(path: Path):
    """Rewrite the .npy header of a partial download so it only claims the complete rows present."""
    with open(path, "r+b") as fh:
        version = np.lib.format.read_magic(fh)
        shape, fortran, dtype = np.lib.format._read_array_header(fh, version)
        data_start = fh.tell()
        row_bytes = int(np.prod(shape[1:])) * dtype.itemsize
        rows = (path.stat().st_size - data_start) // row_bytes
        hdr = {"descr": np.lib.format.dtype_to_descr(dtype), "fortran_order": fortran, "shape": (rows,) + tuple(shape[1:])}
        text = repr(hdr).encode("latin1")
        pre = 10 if version == (1, 0) else 12
        pad = data_start - pre - len(text) - 1
        assert pad >= 0, "new header doesn't fit"
        fh.seek(pre); fh.write(text + b" " * pad + b"\n")
        fh.truncate(data_start + rows * row_bytes)
    return rows, shape


def main():
    import datasets, scipy.io.wavfile
    try:
        progress("Base models")
        for f in ("embedding_model.onnx", "melspectrogram.onnx"):
            fetch(REL + f, OWW_MODELS / f, stage="Base models")
        fetch("https://github.com/rhasspy/piper-sample-generator/releases/download/v2.0.0/en_US-libritts_r-medium.pt",
              W / "piper-sample-generator/models/en_US-libritts_r-medium.pt", stage="Voice generator")

        rir = W / "mit_rirs"
        if not rir.exists() or len(list(rir.glob("*.wav"))) < 200:
            rir.mkdir(exist_ok=True)
            ds = datasets.load_dataset("davidscripka/MIT_environmental_impulse_responses", split="train", streaming=True)
            for k, row in enumerate(ds, 1):
                name = row["audio"]["path"].split("/")[-1]
                scipy.io.wavfile.write(rir / name, 16000, (row["audio"]["array"] * 32767).astype(np.int16))
                if k % 20 == 0:
                    progress("Room echo samples", k, 270)

        fma = W / "fma"; fma.mkdir(exist_ok=True)
        want = FMA_HOURS * 3600 // 30
        have = len(list(fma.glob("*.wav")))
        if have < want:
            ds = datasets.load_dataset("rudraml/fma", name="small", split="train", streaming=True)
            ds = iter(ds.cast_column("audio", datasets.Audio(sampling_rate=16000)))
            for k in range(want):
                row = next(ds)
                if k < have:
                    continue
                name = row["audio"]["path"].split("/")[-1].replace(".mp3", ".wav")
                scipy.io.wavfile.write(fma / name, 16000, (row["audio"]["array"] * 32767).astype(np.int16))
                if k % 10 == 0:
                    progress("Background music", k, want)

        fetch(HF + "validation_set_features.npy", W / "validation_set_features.npy", stage="False-alarm check data")
        acav = W / "acav_4gb.npy"
        if not (W / "acav_4gb.ready").exists():
            fetch(HF + "openwakeword_features_ACAV100M_2000_hrs_16bit.npy", acav, limit=ACAV_BYTES,
                  stage="'Not the wake word' library")
            rows, shape = truncate_npy(acav)
            (W / "acav_4gb.ready").write_text(f"{rows} of {shape[0]} rows")
        progress("All downloads done", 1, 1, finished=True)
    except Exception as e:
        progress("Download failed", note=f"{type(e).__name__}: {e}"[:300], failed=True)
        raise


if __name__ == "__main__":
    main()
