"""Процесс диаризации: python -m transcriber.diar.worker <audio.npy> <число спикеров или 0>."""

from __future__ import annotations

import json
import sys

import numpy as np


def main() -> None:
    from transcriber.config import enforce_offline
    from transcriber.diar import diarize_in_process

    enforce_offline()
    wave = np.load(sys.argv[1])
    n = int(sys.argv[2]) or None

    def progress(f: float) -> None:
        print(f"P {f:.3f}", flush=True)

    turns = diarize_in_process(wave, n, progress)
    print("R " + json.dumps([[t.start, t.end, t.speaker] for t in turns]), flush=True)


if __name__ == "__main__":
    main()
