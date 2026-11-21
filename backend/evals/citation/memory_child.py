"""One measurement for evals.citation.memory (P-49): a process holding only the citation check's ONNX model and its
tokenizer, scoring one batch of (premise, hypothesis) pairs. Run by path in a fresh process, so it imports nothing of
the app: only onnxruntime, tokenizers, numpy and evals.memory_probe.

Usage (by evals.citation.memory):  python evals/citation/memory_child.py JOB.json
The job names the model and tokenizer files, the pairs, the settings, and `lean`: onnxruntime's memory arena and
memory pattern off. It prints one JSON line: memory at the start, once loaded and after scoring, and the probabilities.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # backend/, for evals.memory_probe

from evals.memory_probe import MB, memory_bytes

FEED = {"input_ids": "ids", "attention_mask": "attention_mask", "token_type_ids": "type_ids"}


def main(job_path: str) -> None:
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    steps: dict[str, float] = {}

    def mark(name: str) -> None:
        peak, now = memory_bytes()
        steps[f"{name}_peak_mb"] = round(peak / MB, 1)
        steps[f"{name}_now_mb"] = round(now / MB, 1)

    mark("start")
    import numpy as np
    import onnxruntime as ort
    from tokenizers import Tokenizer

    options = ort.SessionOptions()
    options.log_severity_level = 3
    if job["lean"]:
        options.enable_cpu_mem_arena = False
        options.enable_mem_pattern = False
    session = ort.InferenceSession(job["model"], sess_options=options, providers=["CPUExecutionProvider"])
    tokenizer = Tokenizer.from_file(job["tokenizer"])
    tokenizer.enable_truncation(max_length=job["max_length"])
    pad = tokenizer.token_to_id("[PAD]")
    tokenizer.enable_padding(pad_id=pad if pad is not None else 0, pad_token="[PAD]")
    mark("loaded")
    encoded = tokenizer.encode_batch([(premise, hypothesis) for premise, hypothesis in job["pairs"]])
    inputs = {i.name for i in session.get_inputs()}
    feed = {
        name: np.array([getattr(e, field) for e in encoded], dtype=np.int64)
        for name, field in FEED.items()
        if name in inputs
    }
    logits = np.asarray(session.run(None, feed)[0], dtype=np.float64)
    index = job["entailment_index"]
    probabilities = []
    for row in logits.tolist():
        top = max(row)
        exps = [math.exp(x - top) for x in row]
        probabilities.append(exps[index] / sum(exps))
    mark("scored")
    print(json.dumps({"lean": job["lean"], "memory": steps, "probabilities": probabilities}))


if __name__ == "__main__":
    main(sys.argv[1])
