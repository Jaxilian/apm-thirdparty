#!/usr/bin/env python3
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TOP = "node_modules/vscode-langservers-extracted"


def lock(version, work):
    with open(os.path.join(work, "package.json"), "w") as f:
        json.dump({"name": "x", "version": "1.0.0", "dependencies": {"vscode-langservers-extracted": version}}, f)
    env = {"PATH": os.environ["PATH"], "HOME": work, "npm_config_cache": os.path.join(work, "cache")}
    subprocess.run(["npm", "install", "--package-lock-only", "--ignore-scripts", "--omit=dev"],
                   cwd=work, env=env, check=True, stdout=subprocess.DEVNULL)
    with open(os.path.join(work, "package-lock.json")) as f:
        return json.load(f)["packages"]


def sha256_checked(url, integrity):
    algo, _, b64 = integrity.partition("-")
    with urllib.request.urlopen(url, timeout=120) as r:
        data = r.read()
    if algo != "sha512" or base64.b64encode(hashlib.sha512(data).digest()).decode() != b64:
        sys.exit(f"{url}: does not match its integrity {integrity}")
    return hashlib.sha256(data).hexdigest()


def main():
    if len(sys.argv) != 2 or not re.fullmatch(r"\d+\.\d+\.\d+", sys.argv[1]):
        sys.exit("usage: sources.py VERSION   (rewrites recipe.toml beside it from a fresh npm lock)")
    version = sys.argv[1]
    with tempfile.TemporaryDirectory() as work:
        packages = lock(version, work)
    order = [TOP] + sorted(k for k in packages if k and k != TOP)
    sources, layout = [], []
    for n, key in enumerate(order, 1):
        p = packages[key]
        sha = sha256_checked(p["resolved"], p["integrity"])
        sources.append(f'[[source]]\nurl    = "{p["resolved"]}"\nsha256 = "{sha}"\nstrip  = 1\n')
        if key != TOP:
            layout.append(f"{n} {key[len('node_modules/'):]}")
    path = os.path.join(HERE, "recipe.toml")
    with open(path) as f:
        text = f.read()
    text, a = re.subn(r"(?s)\[\[source\]\].*?(?=\[\[dependencies\]\])", lambda m: "\n".join(sources) + "\n", text, count=1)
    text, b = re.subn(r"(?s)(<<LIST\n).*?(\nLIST\n)", lambda m: m.group(1) + "\n".join(layout) + m.group(2), text, count=1)
    text, c = re.subn(r'(?m)^(version\s*=\s*)"[^"]*"', rf'\g<1>"{version}"', text, count=1)
    text, d = re.subn(r"(?m)^(release\s*=\s*)\d+", r"\g<1>1", text, count=1)
    if (a, b, c, d) != (1, 1, 1, 1):
        sys.exit("recipe.toml: the sources, the LIST, version or release were not found")
    with open(path, "w") as f:
        f.write(text)
    print(f"{len(sources)} sources written; fix the header's count and date, then apm build")


if __name__ == "__main__":
    main()
