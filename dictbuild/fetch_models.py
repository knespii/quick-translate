"""Download the Bergamot cs<->en translation models into data/models/ and pin their hashes.

Usage: python -m dictbuild.fetch_models
"""
import hashlib
import json
import sys

from fxtranslate import discovery

from qtrans.paths import MODELS

PAIRS = [("cs", "en"), ("en", "cs")]


def main() -> int:
    MODELS.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for src, trg in PAIRS:
        route = discovery.add_models(src, trg, cache_dir=str(MODELS), progress=sys.stderr.isatty())
        print(f"{src}->{trg}: {route}")
        d = MODELS / f"{src}-{trg}"
        files = {}
        for p in sorted(d.iterdir()):
            if p.name.startswith("."):
                continue
            files[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
        manifest[f"{src}-{trg}"] = files
    (MODELS / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(json.dumps(manifest, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
