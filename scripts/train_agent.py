"""Train agents/classifiers on the chirp/glitch task and report accuracy.

    python scripts/fetch_training_strain.py --catalog GWTC-3-confident
    python scripts/fetch_glitches.py
    python scripts/train_agent.py --algo ppo cnn --glitch real
"""

from __future__ import annotations

import argparse
import collections
import csv
from pathlib import Path

import numpy as np

from gw_rl.data import CHIRP, CHIRP_MODELS, GLITCH, LABELS, NOISE, event_windows, load_segment, make_dataset

# Held out entirely: their noise is never seen during training.
DEFAULT_TEST_EVENTS = "GW191109_010717,GW200129_065458,GW200224_222234"
MIN_SECONDS = 30


def fit(algo: str, x: np.ndarray, y: np.ndarray, steps: int, seed: int):
    """Return a function mapping spectrograms to predicted labels."""
    if algo in ("mlp", "cnn"):
        from gw_rl.baseline import train_classifier

        return train_classifier(x, y, arch=algo, seed=seed)

    from stable_baselines3 import DQN, PPO

    from gw_rl.env import ChirpGlitchEnv

    env = ChirpGlitchEnv(x, y)
    if algo == "ppo":
        model = PPO("MlpPolicy", env, n_steps=1024, batch_size=256, ent_coef=0.01, seed=seed)
    else:
        model = DQN("MlpPolicy", env, learning_starts=1000, exploration_fraction=0.3, seed=seed)
    model.learn(total_timesteps=steps)
    return lambda obs: model.predict(obs, deterministic=True)[0]


def load_events(data: Path) -> list:
    """One segment per (event, detector), from files of at least MIN_SECONDS."""
    chosen = {}
    for path in sorted(data.glob("*/strain/*.hdf5")):
        key = (path.parent.parent.name, path.name.split("-")[0])
        if int(path.stem.split("-")[-1]) >= MIN_SECONDS and key not in chosen:
            chosen[key] = path
    segments = [load_segment(p) for p in chosen.values()]
    return [s for s in segments if _complete(s)]


def _complete(seg) -> bool:
    """GWOSC marks missing data as NaN, which spreads through whitening."""
    if np.isfinite(seg.x).all():
        return True
    print(f"  skipping {seg.name}: data gap", flush=True)
    return False


def load_glitches(manifest: Path) -> tuple[list, list, list[str]]:
    """Split glitches into train/test by GWOSC hour file, so the two never share noise."""
    rows = list(csv.DictReader(manifest.open()))
    blocks = sorted({int(float(r["gps"])) // 4096 for r in rows})
    test_blocks = set(blocks[::4])
    train, test, test_labels = [], [], []
    gaps = 0
    for row in rows:
        seg = load_segment(row["path"])
        if not np.isfinite(seg.x).all():
            gaps += 1
            continue
        if int(float(row["gps"])) // 4096 in test_blocks:
            test.append(seg)
            test_labels.append(row["label"])
        else:
            train.append(seg)
    print(f"skipped {gaps} glitch clips with data gaps", flush=True)
    return train, test, test_labels


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="data")
    parser.add_argument("--algo", nargs="+", choices=["ppo", "dqn", "mlp", "cnn"], default=["ppo"])
    parser.add_argument("--chirp", choices=sorted(CHIRP_MODELS), default="imr")
    parser.add_argument("--glitch", choices=["real", "synthetic"], default="real",
                        help="train on Gravity Spy glitches or on sine-Gaussian bursts")
    parser.add_argument("--steps", type=int, default=100_000)
    parser.add_argument("--train-per-class", type=int, default=2000)
    parser.add_argument("--test-per-class", type=int, default=300)
    parser.add_argument("--test-events", default=DEFAULT_TEST_EVENTS)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--verbose", action="store_true", help="print confusion and per-type results")
    args = parser.parse_args()

    data = Path(args.data)
    segments = load_events(data)
    if not segments:
        raise SystemExit("No strain files found; run scripts/fetch_training_strain.py first.")
    held_out = set(args.test_events.split(","))
    train = [s for s in segments if s.name.split("/")[0] not in held_out]
    test = [s for s in segments if s.name.split("/")[0] in held_out]

    manifest = data / "glitches" / "manifest.csv"
    glitch_train, glitch_test, glitch_labels = load_glitches(manifest) if manifest.exists() else ([], [], [])
    if args.glitch == "real" and not glitch_train:
        raise SystemExit("No real glitches found; run scripts/fetch_glitches.py or use --glitch synthetic.")
    real = args.glitch == "real"

    rng = np.random.default_rng(args.seed)
    x_train, y_train = make_dataset(
        train, args.train_per_class, rng, chirp=args.chirp, glitches=glitch_train if real else None
    )
    x_test, y_test = make_dataset(
        test, args.test_per_class, rng, chirp=args.chirp, glitches=glitch_test if real else None
    )
    event_names, x_events = event_windows(segments)
    x_glitches = event_windows(glitch_test, before_s=0.5)[1] if glitch_test else None
    print(
        f"{len(segments)} event recordings, {len(glitch_train)} train / {len(glitch_test)} test glitches",
        flush=True,
    )

    for algo in args.algo:
        predict = fit(algo, x_train, y_train, args.steps, args.seed)
        pred = predict(x_test)
        pred_events = predict(x_events)
        line = (
            f"chirp={args.chirp} glitch={args.glitch} seed={args.seed} algo={algo:4s} "
            f"held_out={np.mean(pred == y_test):.1%} "
            f"events_found={np.sum(pred_events == CHIRP)}/{len(event_names)}"
        )
        if x_glitches is not None:
            pred_glitches = predict(x_glitches)
            blips = np.array([label == "Blip" for label in glitch_labels])
            line += (
                f" real_glitches: glitch={np.mean(pred_glitches == GLITCH):.0%}"
                f" chirp={np.mean(pred_glitches == CHIRP):.0%}"
                f" noise={np.mean(pred_glitches == NOISE):.0%}"
                f" blips_as_chirp={np.sum(pred_glitches[blips] == CHIRP)}/{blips.sum()}"
            )
        print(line, flush=True)

        if args.verbose:
            print("  held-out confusion (rows = truth, cols = prediction):", *LABELS)
            for truth, name in enumerate(LABELS):
                row = [int(np.sum((y_test == truth) & (pred == p))) for p in range(len(LABELS))]
                print(f"    {name:7s}", *(f"{v:4d}" for v in row))
            missed = [n for n, p in zip(event_names, pred_events) if p != CHIRP]
            print(f"  events not called chirp ({len(missed)}):", ", ".join(missed))
            if x_glitches is not None:
                print("  real test glitches by type:  n  glitch  chirp  noise")
                by_type = collections.defaultdict(list)
                for label, p in zip(glitch_labels, pred_glitches):
                    by_type[label].append(p)
                for label, ps in sorted(by_type.items(), key=lambda kv: -len(kv[1])):
                    ps = np.array(ps)
                    print(f"    {label:20s} {len(ps):3d}" + "".join(
                        f"  {np.mean(ps == c):5.0%}" for c in (GLITCH, CHIRP, NOISE)))


if __name__ == "__main__":
    main()
