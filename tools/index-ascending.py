#!/usr/bin/env python3
import hashlib
import os
import re
import sys
import tomllib
import zipfile

HEADER = "#apm-index 1"
STAMPED = ("index_path", "index_sha256", "index_size")


def ascending(tsv):
    lines = tsv.split("\n")
    if lines[0] != HEADER:
        raise SystemExit("index-ascending: not an apm index (%r)" % lines[0])
    body = [l for l in lines[1:] if l]
    groups = []
    for line in body:
        f = line.split("\t")
        key = (f[1], f[2])
        if groups and groups[-1][0] == key:
            groups[-1][1].append(line)
        else:
            groups.append((key, [line]))
    seen = [k for k, _ in groups]
    if len(seen) != len(set(seen)):
        raise SystemExit("index-ascending: a package's entries are not together; apm index changed its order")
    out = [HEADER]
    for _, rows in groups:
        out.extend(reversed(rows))
    return "\n".join(out) + "\n"


def rezip(path, members):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members:
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data)


def restamp(text, values):
    for key in STAMPED:
        value = values[key]
        shown = str(value) if isinstance(value, int) else '"%s"' % value
        text, n = re.subn(r"(?m)^%s = .*$" % key, "%s = %s" % (key, shown), text)
        if n != 1:
            raise SystemExit("index-ascending: stamp.toml has no single %s" % key)
    return text


def run(index):
    stamp_path = os.path.join(index, "stamp.toml")
    with open(stamp_path) as f:
        stamp_text = f.read()
    stamp = tomllib.loads(stamp_text)
    old = os.path.join(index, stamp["index_path"])
    with zipfile.ZipFile(old) as z:
        members = [(i.filename, z.read(i.filename)) for i in z.infolist()]
    tsv = dict(members)["index.tsv"].decode()
    fixed = ascending(tsv)
    if sorted(fixed.split("\n")) != sorted(tsv.rstrip("\n").split("\n") + [""]):
        raise SystemExit("index-ascending: the reordered index lost or gained a line")
    members = [(n, fixed.encode() if n == "index.tsv" else d) for n, d in members]
    part = os.path.join(index, "index.zip.part")
    rezip(part, members)
    with open(part, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    name = "index-%s.zip" % sha[:16]
    os.replace(part, os.path.join(index, name))
    if name != stamp["index_path"]:
        os.remove(old)
    values = {"index_path": name, "index_sha256": sha, "index_size": os.path.getsize(os.path.join(index, name))}
    with open(stamp_path, "w") as f:
        f.write(restamp(stamp_text, values))
    print("index-ascending: %s -> %s, each package's builds oldest first" % (stamp["index_path"], name))


def selftest():
    tsv = HEADER + "\napkg\ta\tx\t2.0.0\nrecipe\ta\tx\t1.0.0\napkg\tb\ty\t1.0.0\nrecipe\tm\tv\t2026.6.0\napkg\tm\tv\t2026.5.0\n"
    got = ascending(tsv).split("\n")
    assert got == [HEADER, "recipe\ta\tx\t1.0.0", "apkg\ta\tx\t2.0.0", "apkg\tb\ty\t1.0.0",
                   "apkg\tm\tv\t2026.5.0", "recipe\tm\tv\t2026.6.0", ""]
    try:
        ascending(HEADER + "\napkg\ta\tx\t1\napkg\tb\ty\t1\napkg\ta\tx\t2\n")
        raise AssertionError("scattered entries were accepted")
    except SystemExit:
        pass
    text = 'index_version = 1\nindex_path = "index-aa.zip"\nindex_sha256 = "aa"\nindex_size = 1\npackages = 3\n'
    out = restamp(text, {"index_path": "index-bb.zip", "index_sha256": "bb", "index_size": 9})
    assert tomllib.loads(out) == {"index_version": 1, "index_path": "index-bb.zip", "index_sha256": "bb", "index_size": 9, "packages": 3}
    print("selftest ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        selftest()
    elif len(sys.argv) == 2:
        run(sys.argv[1])
    else:
        raise SystemExit("usage: index-ascending.py INDEX_DIR | --selftest")
