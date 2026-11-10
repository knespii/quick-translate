"""Download the raw dictionary sources into data/raw/ (skips files that are already there).

Usage: python -m dictbuild.fetch [--force] [name ...]
"""
import gzip
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

from qtrans.paths import RAW
from dictbuild.sources import SOURCES

UA = "quick-translate/0.1 (personal dictionary build)"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path, attempts: int = 4) -> None:
    delay = 3
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as out:
                total = int(r.headers.get("Content-Length") or 0)
                done = 0
                while chunk := r.read(1 << 20):
                    out.write(chunk)
                    done += len(chunk)
                    if total:
                        print(f"\r  {done / 1e6:6.1f} / {total / 1e6:.1f} MB", end="", file=sys.stderr)
            print(file=sys.stderr)
            return
        except Exception as e:  # network errors, Wayback "Temporarily Offline" (HTTP 503), timeouts
            print(f"  attempt {i + 1}/{attempts} failed: {e}", file=sys.stderr)
            dest.unlink(missing_ok=True)
            if i + 1 < attempts:
                time.sleep(delay)
                delay *= 2
    raise RuntimeError(f"download failed: {url}")


def fetch_source(src: dict, force: bool = False) -> dict:
    final = RAW / src["file"]
    if final.exists() and not force:
        print(f"[{src['name']}] already present, skipping")
        return {"file": src["file"], "sha256": sha256_file(final), "note": "cached"}
    errors = []
    for m in src["mirrors"]:
        tmp = final.with_name(final.name + ".part")
        print(f"[{src['name']}] {m['url']}")
        try:
            download(m["url"], tmp)
            if m.get("sha256") and sha256_file(tmp) != m["sha256"]:
                raise RuntimeError("sha256 mismatch")
            if m.get("gzip"):
                unpacked = final.with_name(final.name + ".unpacked")
                with gzip.open(tmp, "rb") as g, open(unpacked, "wb") as out:
                    while chunk := g.read(1 << 20):
                        out.write(chunk)
                tmp.unlink()
                tmp = unpacked
                if m.get("sha256_unpacked") and sha256_file(tmp) != m["sha256_unpacked"]:
                    raise RuntimeError("sha256 mismatch after unpacking")
            tmp.replace(final)
            return {"file": src["file"], "sha256": sha256_file(final), "url": m["url"], "license": src["license"]}
        except Exception as e:
            errors.append(f"{m['url']}: {e}")
            print(f"  mirror failed: {e}", file=sys.stderr)
            tmp.unlink(missing_ok=True)
    raise RuntimeError(f"[{src['name']}] all mirrors failed:\n  " + "\n  ".join(errors))


def main(argv: list[str]) -> int:
    force = "--force" in argv
    names = [a for a in argv if not a.startswith("--")]
    RAW.mkdir(parents=True, exist_ok=True)
    manifest_path = RAW / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    failed = []
    for src in SOURCES:
        if names and src["name"] not in names:
            continue
        try:
            info = fetch_source(src, force)
            manifest[src["name"]] = {**manifest.get(src["name"], {}), **info}
        except RuntimeError as e:
            print(e, file=sys.stderr)
            if not src.get("optional"):
                failed.append(src["name"])
    manifest_path.write_text(json.dumps(manifest, indent=1, ensure_ascii=False))
    if failed:
        print("FAILED: " + ", ".join(failed), file=sys.stderr)
        return 1
    print("all sources ready in", RAW)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
