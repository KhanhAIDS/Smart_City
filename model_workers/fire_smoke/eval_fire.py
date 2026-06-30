import glob
import os
import sys

from collections import deque

from huggingface_hub import hf_hub_download, list_repo_files
from PIL import Image
from ultralytics import YOLO

FRAMES_DIR = os.getenv("EVAL_FRAMES", "/app/eval_frames")
FPS = float(os.getenv("EVAL_FPS", "5"))
MODEL_CONF_FLOOR = float(os.getenv("EVAL_CONF_FLOOR", "0.05"))
IMG_SIZE = int(os.getenv("FIRE_IMG_SIZE", "640"))

GT_SMOKE_S = 9.0
GT_FIRE_S = 16.0
VIDEO_END_S = 20.0
TOL = 2.5


def classify(name):
    low = str(name).lower()
    if "smoke" in low:
        return "smoke"
    if "fire" in low or "flame" in low:
        return "fire"
    return "other"


def resolve(repo):
    files = list_repo_files(repo)
    pts = [f for f in files if f.endswith(".pt")]
    pts.sort(key=lambda f: ("best" not in f.lower(), len(f)))
    return hf_hub_download(repo_id=repo, filename=pts[0])


EVAL_DEVICE = os.getenv("EVAL_DEVICE", "cpu")


def run_model(repo):
    path = resolve(repo)
    model = YOLO(path)
    names = model.names if isinstance(model.names, dict) else dict(enumerate(model.names))
    kind = {int(c): classify(n) for c, n in names.items()}

    frames = sorted(glob.glob(os.path.join(FRAMES_DIR, "*.jpg")))
    per_frame = []
    for idx, fp in enumerate(frames):
        ts = idx / FPS
        img = Image.open(fp).convert("RGB")
        res = model.predict(img, conf=MODEL_CONF_FLOOR, imgsz=IMG_SIZE,
                            device=EVAL_DEVICE, verbose=False)
        fire_c = 0.0
        smoke_c = 0.0
        for r in res:
            if r.boxes is None:
                continue
            for c, k in zip(r.boxes.conf.cpu().tolist(), r.boxes.cls.cpu().tolist()):
                kd = kind.get(int(k), "other")
                if kd == "fire":
                    fire_c = max(fire_c, float(c))
                elif kd == "smoke":
                    smoke_c = max(smoke_c, float(c))
        per_frame.append((ts, fire_c, smoke_c))
    return names, per_frame


def first_persist(per_frame, kind_idx, conf, n, m):
    win = deque(maxlen=m)
    for ts, fire_c, smoke_c in per_frame:
        val = (fire_c if kind_idx == 0 else smoke_c) >= conf
        win.append(val)
        if sum(win) >= n:
            return ts
    return None


def pre_window_fp(per_frame, kind_idx, conf, n, m, before_s):
    win = deque(maxlen=m)
    fires = 0
    for ts, fire_c, smoke_c in per_frame:
        if ts >= before_s:
            break
        val = (fire_c if kind_idx == 0 else smoke_c) >= conf
        win.append(val)
        if sum(win) >= n:
            fires += 1
    return fires


def evaluate(repo):
    print(f"\n===== {repo} =====", flush=True)
    names, per_frame = run_model(repo)
    print(f"names={names}")
    if os.getenv("EVAL_TIMELINE"):
        for ts, f, s in per_frame:
            if ts >= 7.0:
                print(f"  t={ts:5.1f}s fire={f:.2f} smoke={s:.2f}", flush=True)
    raw_smoke = [(ts, s) for ts, f, s in per_frame if s > 0.05]
    raw_fire = [(ts, f) for ts, f, s in per_frame if f > 0.05]
    print(f"raw smoke>0.05 first@{raw_smoke[0][0]:.1f}s n={len(raw_smoke)} "
          if raw_smoke else "raw smoke>0.05: none ")
    print(f"raw fire>0.05  first@{raw_fire[0][0]:.1f}s n={len(raw_fire)} "
          if raw_fire else "raw fire>0.05: none ")

    best = None
    for conf in [0.25, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60]:
        for m in [4, 5, 6]:
            for n in [2, 3, 4]:
                if n > m:
                    continue
                smoke_t = first_persist(per_frame, 1, conf, n, m)
                fire_t = first_persist(per_frame, 0, conf, n, m)
                smoke_fp = pre_window_fp(per_frame, 1, conf, n, m, GT_SMOKE_S - TOL)
                fire_fp = pre_window_fp(per_frame, 0, conf, n, m, GT_FIRE_S - TOL)
                smoke_ok = smoke_t is not None and abs(smoke_t - GT_SMOKE_S) <= 4.0
                fire_ok = fire_t is not None and abs(fire_t - GT_FIRE_S) <= 4.0
                score = 0
                score += 2 if smoke_ok else 0
                score += 2 if fire_ok else 0
                score -= smoke_fp + fire_fp
                cand = (score, -(smoke_fp + fire_fp), conf, n, m,
                        smoke_t, fire_t, smoke_fp, fire_fp)
                if best is None or cand[:2] > best[:2]:
                    best = cand
        # print a representative row per conf at n=3,m=5
        st = first_persist(per_frame, 1, conf, 3, 5)
        ft = first_persist(per_frame, 0, conf, 3, 5)
        sfp = pre_window_fp(per_frame, 1, conf, 3, 5, GT_SMOKE_S - TOL)
        ffp = pre_window_fp(per_frame, 0, conf, 3, 5, GT_FIRE_S - TOL)
        print(f"  conf={conf:.2f} N3/M5: smoke@{st} fire@{ft} "
              f"pre-fp(smoke<{GT_SMOKE_S - TOL:.1f}s)={sfp} "
              f"pre-fp(fire<{GT_FIRE_S - TOL:.1f}s)={ffp}")

    if best:
        (score, _, conf, n, m, st, ft, sfp, ffp) = best
        print(f"  >> BEST conf={conf} N={n} M={m} smoke@{st} fire@{ft} "
              f"smoke_fp={sfp} fire_fp={ffp} score={score}")

    chosen_conf = float(os.getenv("CHOSEN_CONF", "0.40"))
    chosen_n = int(os.getenv("CHOSEN_N", "2"))
    chosen_m = int(os.getenv("CHOSEN_M", "5"))
    cst = first_persist(per_frame, 1, chosen_conf, chosen_n, chosen_m)
    cft = first_persist(per_frame, 0, chosen_conf, chosen_n, chosen_m)
    csfp = pre_window_fp(per_frame, 1, chosen_conf, chosen_n, chosen_m, GT_SMOKE_S - TOL)
    cffp = pre_window_fp(per_frame, 0, chosen_conf, chosen_n, chosen_m, GT_FIRE_S - TOL)
    csfp9 = pre_window_fp(per_frame, 1, chosen_conf, chosen_n, chosen_m, GT_SMOKE_S)
    cffp9 = pre_window_fp(per_frame, 0, chosen_conf, chosen_n, chosen_m, GT_SMOKE_S)
    print(f"  >> CHOSEN conf={chosen_conf} N={chosen_n} M={chosen_m}: "
          f"smoke@{cst} fire@{cft} | pre-9s FP smoke={csfp9} fire={cffp9} | "
          f"pre-GT FP smoke={csfp} fire={cffp}")
    return best


if __name__ == "__main__":
    repos = sys.argv[1:] or [
        "rabahdev/fire-smoke-yolov8n",
        "odiug77/wildfire-smoke-fire",
        "Mehedi-2-96/fire-smoke-detection-yolo",
    ]
    for repo in repos:
        try:
            evaluate(repo)
        except Exception as exc:  # noqa: BLE001
            print(f"EVAL_FAIL {repo} -> {type(exc).__name__}: {exc}", flush=True)
