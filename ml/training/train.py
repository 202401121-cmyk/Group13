"""Fine-tune the pair matcher on CPU (US-15, S3-4).

    python ml/training/train.py [--epochs 3] [--batch 32] [--lr 4e-5] [--limit N] [--max-epoch-minutes 45]

A cross-encoder (cross-encoder/ms-marco-MiniLM-L6-v2, 22.7M parameters, Apache-2.0) reads the two titles together and
gives one logit: the same product or not. Data: ml/scripts/matcher_data.py (Abt-Buy, Amazon-Google and our own train
split; never WDC, never Reddit text). Seeded; a checkpoint after each epoch in ml/models/matcher_v1/ (git-ignored);
re-running resumes from the last finished one. If one epoch is projected to take longer than --max-epoch-minutes it
stops before wasting the time, and the run belongs on Colab instead.
"""

from __future__ import annotations

import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / "scripts"))  # ml/scripts holds the shared code

import argparse
import json
import random
import time
from datetime import UTC, datetime
from pathlib import Path

import torch
from checkpoints import checkpoint_dir, latest_checkpoint, mark_done
from common import BASE_MODEL, MAX_LENGTH, MODEL_DIR, SEED, load, score
from matcher_data import LABELLING_DIR, build_all
from matcher_threshold import choose_threshold
from pairsets import DEFAULT_RAW_DIR

RECORD = Path(__file__).resolve().parents[1] / "results" / "matcher_v1_training.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Fine-tune the pair matcher on CPU.")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch", type=int, default=32)
    parser.add_argument("--lr", type=float, default=4e-5)
    parser.add_argument("--limit", type=int, default=0, help="train on only the first N pairs (a smoke test)")
    parser.add_argument("--max-epoch-minutes", type=float, default=45.0)
    parser.add_argument("--out", type=Path, default=MODEL_DIR)
    args = parser.parse_args()

    random.seed(SEED)
    torch.manual_seed(SEED)
    data = build_all(DEFAULT_RAW_DIR, LABELLING_DIR, SEED)
    train = data.train[: args.limit] if args.limit else data.train
    dev = data.dev
    print(f"train {len(train)} pairs ({sum(p.label for p in train)} matches), dev {len(dev)}; {data.counts}")

    resumed = latest_checkpoint(args.out)
    start_epoch = resumed[0] if resumed else 0
    model, tokenizer = load(resumed[1] if resumed else BASE_MODEL)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    steps_per_epoch = (len(train) + args.batch - 1) // args.batch
    total = steps_per_epoch * args.epochs
    scheduler = torch.optim.lr_scheduler.LambdaLR(
        optimizer, lambda step: min(1.0, (step + 1) / (0.06 * total)) * max(0.0, (total - step) / total)
    )
    if resumed and (resumed[1] / "optimizer.pt").is_file():
        state = torch.load(resumed[1] / "optimizer.pt")
        optimizer.load_state_dict(state["optimizer"])
        scheduler.load_state_dict(state["scheduler"])
    print(f"resuming after epoch {start_epoch}" if resumed else f"starting from {BASE_MODEL}")
    loss_fn = torch.nn.BCEWithLogitsLoss()

    history: list[dict[str, float | int]] = []
    for epoch in range(start_epoch + 1, args.epochs + 1):
        model.train()
        order = list(range(len(train)))
        random.Random(f"{SEED}|{epoch}").shuffle(order)
        started = time.monotonic()
        total_loss = 0.0
        for step in range(steps_per_epoch):
            chunk = [train[i] for i in order[step * args.batch : (step + 1) * args.batch]]
            batch = tokenizer(  # type: ignore[operator]
                [p.left for p in chunk],
                [p.right for p in chunk],
                truncation=True,
                max_length=MAX_LENGTH,
                padding=True,
                return_tensors="pt",
            )
            labels = torch.tensor([float(p.label) for p in chunk])
            loss = loss_fn(model(**batch).logits[:, 0], labels)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            optimizer.zero_grad()
            total_loss += loss.item()
            if step == 19:
                projected = (time.monotonic() - started) / 20 * steps_per_epoch / 60
                print(f"epoch {epoch}: 20 steps done, projected {projected:.1f} minutes per epoch", flush=True)
                if projected > args.max_epoch_minutes:
                    print("too slow for this machine: train on Colab instead (training run pending: Colab)")
                    return 3
            if step % 25 == 0:
                print(f"epoch {epoch} step {step}/{steps_per_epoch} loss {loss.item():.4f}", flush=True)
        minutes = (time.monotonic() - started) / 60
        scores = score(model, tokenizer, [(p.left, p.right) for p in dev])
        choice = choose_threshold(scores, [p.label for p in dev])
        print(
            f"epoch {epoch} done in {minutes:.1f} min, mean loss {total_loss / steps_per_epoch:.4f}; dev threshold "
            f"{choice.threshold:.3f} precision {choice.precision:.3f} recall {choice.recall:.3f} met {choice.met}",
            flush=True,
        )
        history.append(
            {
                "epoch": epoch,
                "minutes": round(minutes, 2),
                "mean_loss": round(total_loss / steps_per_epoch, 4),
                "dev_threshold": round(choice.threshold, 4),
                "dev_precision": round(choice.precision, 4),
                "dev_recall": round(choice.recall, 4),
            }
        )
        folder = checkpoint_dir(args.out, epoch)
        folder.mkdir(parents=True, exist_ok=True)
        model.save_pretrained(folder)
        tokenizer.save_pretrained(folder)  # type: ignore[attr-defined]
        torch.save({"optimizer": optimizer.state_dict(), "scheduler": scheduler.state_dict()}, folder / "optimizer.pt")
        (folder / "epoch.json").write_text(
            json.dumps({"epoch": epoch, "minutes": round(minutes, 2), "seed": SEED, "base": BASE_MODEL}),
            encoding="utf-8",
        )
        mark_done(folder)
    record = {
        "date": datetime.now(UTC).date().isoformat(),
        "base_model": BASE_MODEL,
        "seed": SEED,
        "epochs": args.epochs,
        "batch": args.batch,
        "learning_rate": args.lr,
        "train_pairs": len(train),
        "dev_pairs": len(dev),
        "counts": data.counts,
        "resumed_after_epoch": start_epoch,
        "history": history,
    }
    RECORD.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
