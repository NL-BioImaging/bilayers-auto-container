"""Re-download the vendored bilayers modules from the pinned upstream commit.

bilayers is not on PyPI and its repository cannot be checked out on Windows
(a docs image has ':' in its name), so the two modules we need are vendored
unmodified. Bump BILAYERS_COMMIT and run this script to update them.
"""
import hashlib
import json
import urllib.request
from pathlib import Path

BILAYERS_REPO = "bilayer-containers/bilayers"
BILAYERS_COMMIT = "ac0bd48660c9576ad5f20f724b3bb47f68802ddb"
FILES = {
    "src/bilayers/parse.py": "parse.py",
    "src/bilayers/cli_generator.py": "cli_generator.py",
    "LICENSE": "LICENSE",
}
VENDOR_DIR = Path(__file__).resolve().parents[1] / "src" / "bilayers_auto" / "_vendor" / "bilayers"


def main():
    hashes = {}
    for upstream, local in FILES.items():
        url = f"https://raw.githubusercontent.com/{BILAYERS_REPO}/{BILAYERS_COMMIT}/{upstream}"
        with urllib.request.urlopen(url) as response:
            content = response.read()
        (VENDOR_DIR / local).write_bytes(content)
        hashes[local] = hashlib.sha256(content).hexdigest()
        print(f"{url} -> {local}")
    upstream_info = {"repository": f"https://github.com/{BILAYERS_REPO}", "commit": BILAYERS_COMMIT, "sha256": hashes}
    (VENDOR_DIR / "UPSTREAM.json").write_text(json.dumps(upstream_info, indent=2) + "\n")


if __name__ == "__main__":
    main()
