"""Run every test file in this folder and summarise.

    python backend/tests/run_all.py            # everything
    python backend/tests/run_all.py split leg  # only files whose name contains "split" or "leg"

Each test_*.py is a self-contained script (no pytest, no network, no Redis, no
Supabase), so each runs in its own process: one file's module-level state —
patched clients, env vars, fake fixtures — can never leak into another's.

Exit code 0 only if every selected file passed.
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# Tests run from backend/: several read source files by relative path
# (app/core/...) or rely on backend/.env being in the working directory.
BACKEND = os.path.dirname(HERE)


def main(argv):
    names = sorted(f for f in os.listdir(HERE) if f.startswith("test_") and f.endswith(".py"))
    if argv:
        names = [n for n in names if any(a in n for a in argv)]
    if not names:
        print("no test files matched")
        return 1

    env = dict(os.environ, PYTHONIOENCODING="utf-8")   # several tests print non-ASCII
    failed = []
    t0 = time.time()
    for n in names:
        start = time.time()
        try:
            r = subprocess.run([sys.executable, os.path.join(HERE, n)], cwd=BACKEND, env=env,
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=300)
            ok, out = r.returncode == 0, (r.stdout or "") + (r.stderr or "")
        except subprocess.TimeoutExpired:
            ok, out = False, "timed out after 300s"
        print(f"  {'PASS' if ok else 'FAIL'}  {n:<38} {time.time() - start:5.1f}s")
        if not ok:
            failed.append(n)
            tail = [l for l in out.splitlines() if "FAIL" in l or "Error" in l or "Traceback" in l][:6]
            for l in tail:
                print(f"          {l.strip()[:160]}")

    print(f"\n{len(names) - len(failed)}/{len(names)} files passed in {time.time() - t0:.0f}s")
    if failed:
        print("failed: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
