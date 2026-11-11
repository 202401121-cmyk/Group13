"""Time 1,000 pairs on CPU with onnxruntime alone, and record peak memory (US-15, S3-4, DECIDED 7).

    python ml/training/measure_onnx.py ml/models/matcher_v1/onnx

Run in its own process with only onnxruntime and the tokenizer loaded, so the memory is the matcher's and not PyTorch's.
Prints one JSON line.
"""

import json
import sys
import threading
import time

import numpy as np
import onnxruntime as ort
import psutil
from tokenizers import Tokenizer

folder = sys.argv[1]
tok = Tokenizer.from_file(folder + "/tokenizer.json")
tok.enable_truncation(128)
tok.enable_padding()
s = ort.InferenceSession(folder + "/model.onnx", providers=["CPUExecutionProvider"])
names = {i.name for i in s.get_inputs()}
pairs = [
    ("Nova Book 14 16GB 512GB silver laptop", "Nova Book 14 Laptop 16 GB / 512 GB SSD"),
    ("Kettle Pro 1.7L Steel", "Kettle Pro 1.7 L Steel Grey"),
] * 500


def run(chunk):
    enc = tok.encode_batch(chunk)
    feed = {
        "input_ids": np.array([e.ids for e in enc], dtype=np.int64),
        "attention_mask": np.array([e.attention_mask for e in enc], dtype=np.int64),
        "token_type_ids": np.array([e.type_ids for e in enc], dtype=np.int64),
    }
    return s.run(["logits"], {k: v for k, v in feed.items() if k in names})


run(pairs[:32])
p = psutil.Process()
peak = [p.memory_info().rss]
stop = threading.Event()


def watch():
    while not stop.is_set():
        peak[0] = max(peak[0], p.memory_info().rss)
        time.sleep(0.005)


t = threading.Thread(target=watch)
t.start()
a = time.perf_counter()
for i in range(0, 1000, 32):
    run(pairs[i : i + 32])
sec = time.perf_counter() - a
stop.set()
t.join()
print(
    json.dumps(
        {"seconds_for_1000_pairs": round(sec, 2), "peak_memory_mb": round(max(peak[0], p.memory_info().rss) / 1e6)}
    )
)
