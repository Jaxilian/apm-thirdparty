#!/usr/bin/env python3
"""Bump the recipes to their newest upstream versions.

    python3 -I bump.py                   dry run: a table, recipe / current / newest / action
    python3 -I bump.py --write           rewrite every recipe that has a newer upstream
    python3 -I bump.py --only org/name   one recipe (repeatable)
    python3 -I bump.py --dl DIR          where the downloads go (default: $TMPDIR/apm-bump)
    python3 -I bump.py --selftest        the checks of the pure parts
    python3 -I bump.py --root DIR        the recipes of another index (DIR/recipes/...):
                                         apm-recipes is bumped by this same script

Standard library only. GITHUB_TOKEN in the environment raises GitHub's API
rate limit (60/h without it; `GITHUB_TOKEN=$(gh auth token)` locally).

A recipe takes part when an `update.toml` sits beside its recipe.toml: apm
reads recipe.toml with deny_unknown_fields, so the rule cannot live in it,
and publish.sh copies only recipe.toml into the index. The sidecar is a
list of [[source]] tables, the Nth for the recipe's Nth [[source]] (or
`source = N` to say which); a source without a rule stays as it is.

Keys every kind takes:

    kind          github | apt | regex | redirect | json | node | dotnet
    track         stay on this line: "24", "3.13", "21" -- a prefix of the
                  apm version; candidates outside it are not considered
    version_from  regex over the upstream version string whose groups,
                  joined by dots, make the apm version. Without it: the
                  first numeric run, an epoch dropped, split on dots, each
                  part an integer (no leading zeros), the first three, padded
                  with 0 -- 156.0 -> 156.0.0, 26.08.1 -> 26.8.1,
                  1.135.06055 -> 1.135.6055, 1.8.2.5 -> 1.8.2,
                  1:1.3.4.258.gc60b -> 1.3.4. Chrome's 155.0.8059.39 ->
                  155.8059.39 and Steam's 1.0.0.87 -> 1.0.87 need the regex.
    url           template of the source URL, when the kind has no other way
    sums          URL template of a text that lists the file's sha256 (or
                  sha512): SHA256SUMS, a .sha256 file, a mirrorlist page.
                  When given, a download whose hash is not in it is refused.

Templates take {version} (upstream, as the vendor writes it), {v1} {v2} {v3}
(its dot-separated parts, as written: 26.08.1 gives v2 = 08), {tag}
(github), {dir} (regex with a dir step) and, in `sums`, {url}.

The kinds:

    github    repo = "owner/name". tag = regex over the tag names, one group
              = the upstream version; prereleases and drafts are skipped.
              asset = regex over the release's asset names (one group in it:
              the version comes from the asset name instead, as for a
              date-tagged release). url = template when the file is not an
              asset (archive/refs/tags/{tag}.tar.gz). The checksum is the
              digest GitHub records for the asset. releases = how many of
              the newest releases to look at (30; fewer for a repository
              whose releases carry hundreds of assets). rebuild = false
              leaves a release that carries the same version again (a
              rebuilt interpreter under a new date tag) alone.
    apt       packages = URL of a Packages, Packages.xz or Packages.gz index;
              package = the name; base = the repository root (default: the
              part before /dists/). Version, Filename and SHA256 come from
              the index; the newest version in Debian's ordering.
    regex     page = URL, regex = one group, the version; the newest match
              wins. dir_page and dir_regex first choose {dir} the same way
              (a release directory per line, as download.kde.org has them).
    redirect  page = URL that redirects to the versioned file; regex = one
              group over the final URL, which becomes the source URL.
    json      page = URL of a JSON document; version_key, url_key, sha256_key,
              sums_key = dotted paths into it (a number indexes a list);
              url = template instead of url_key.
    node      nodejs.org/dist/index.json: the newest LTS release.
    dotnet    the .NET release metadata: the latest SDK of the active LTS
              channel (track = "10.0" names a channel instead).

What a bump writes: version, the moved sources' url and sha256, release = 1
for a new version, and the old file name wherever the recipe spells it out
(an AppImage's prepare line). A source that moved while the version did not
(a Debian library, a tarball respun under the same version) gets release + 1.
Prose in the comments is left alone; the table says where each checksum came
from.
"""
import argparse
import functools
import gzip
import hashlib
import itertools
import json
import lzma
import os
import re
import sys
import tempfile
import tomllib
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "apm-thirdparty-bump (+https://github.com/Jaxilian/apm-thirdparty)"


class Skip(Exception):
    """This recipe cannot be bumped now; the reason is the message."""


# -- HTTP --------------------------------------------------------------------

_pages = {}


def request(url, method="GET"):
    req = urllib.request.Request(url, method=method, headers={"User-Agent": UA})
    if url.startswith("https://api.github.com/") and os.environ.get("GITHUB_TOKEN"):
        req.add_header("Authorization", "Bearer " + os.environ["GITHUB_TOKEN"])
    return urllib.request.urlopen(req, timeout=120)


def page(url):
    """The text at url, decompressed when it is .xz or .gz; cached per run."""
    if url not in _pages:
        with request(url) as r:
            data = r.read()
        if url.endswith(".xz"):
            data = lzma.decompress(data)
        elif url.endswith(".gz"):
            data = gzip.decompress(data)
        _pages[url] = data.decode("utf-8", "replace")
    return _pages[url]


def final_url(url):
    """Where url redirects to (HEAD, redirects followed)."""
    with request(url, method="HEAD") as r:
        return r.geturl()


def download(url, directory):
    """Fetch url into directory; (path, sha256, sha512)."""
    os.makedirs(directory, exist_ok=True)
    name = urllib.parse.unquote(url.rsplit("/", 1)[-1])
    path = os.path.join(directory, name)
    h256, h512 = hashlib.sha256(), hashlib.sha512()
    with request(url) as r, open(path, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
            h256.update(chunk)
            h512.update(chunk)
    return path, h256.hexdigest(), h512.hexdigest()


# -- versions ----------------------------------------------------------------

def nums(s):
    """Every number in s, for ordering candidates: 1.135.06055 -> (1, 135, 6055)."""
    return tuple(int(x) for x in re.findall(r"\d+", s))


def apm_version(upstream, rule):
    """The recipe's X.Y.Z for an upstream version string (see the module doc)."""
    if "version_from" in rule:
        m = re.search(rule["version_from"], upstream)
        if not m:
            raise Skip(f"version_from {rule['version_from']!r} does not match {upstream!r}")
        parts = [int(g) for g in m.groups()]
    else:
        m = re.search(r"\d+(?:\.\d+)*", re.sub(r"^\d+:", "", upstream))
        if not m:
            raise Skip(f"no version in {upstream!r}")
        parts = [int(x) for x in m.group(0).split(".")][:3]
    parts += [0] * (3 - len(parts))
    return ".".join(str(p) for p in parts[:3])


def in_track(upstream, rule):
    if "track" not in rule:
        return True
    try:
        v = apm_version(upstream, rule)
    except Skip:
        return False
    return v == rule["track"] or v.startswith(rule["track"] + ".")


def _deb_order(c):
    if c == "~":
        return -1
    if c == "":
        return 0
    return ord(c) if c.isalpha() else ord(c) + 256


def _deb_frag(a, b):
    while a or b:
        da, db = re.match(r"\D*", a).group(0), re.match(r"\D*", b).group(0)
        a, b = a[len(da):], b[len(db):]
        for x, y in itertools.zip_longest(da, db, fillvalue=""):
            if x != y:
                return _deb_order(x) - _deb_order(y)
        na, nb = re.match(r"\d*", a).group(0), re.match(r"\d*", b).group(0)
        a, b = a[len(na):], b[len(nb):]
        if int(na or 0) != int(nb or 0):
            return int(na or 0) - int(nb or 0)
    return 0


def deb_cmp(a, b):
    """dpkg --compare-versions: epoch, upstream, revision; ~ sorts first."""
    def split(v):
        epoch, _, rest = v.partition(":") if ":" in v else ("0", "", v)
        up, _, rev = rest.rpartition("-") if "-" in rest else (rest, "", "")
        return int(epoch), up, rev
    ea, ua, ra = split(a)
    eb, ub, rb = split(b)
    return (ea - eb) or _deb_frag(ua, ub) or _deb_frag(ra, rb)


def expand(template, version, extra):
    m = {"version": version, **extra}
    for i, p in enumerate(version.split(".")[:3], 1):
        m[f"v{i}"] = p
    return template.format_map(m)


# -- finders: rule -> {version, url, sha256?, where?, sums?, vars} -----------

def find_github(rule):
    rels = json.loads(page(f"https://api.github.com/repos/{rule['repo']}/releases?per_page={rule.get('releases', 30)}"))
    cands = []
    for r in rels:
        if r.get("prerelease") or r.get("draft"):
            continue
        m = re.search(rule["tag"], r["tag_name"])
        if m:
            cands.append((m.group(1), r))
    if not cands:
        raise Skip(f"no release of {rule['repo']} matches tag regex {rule['tag']!r}")
    cands.sort(key=lambda c: nums(c[0]), reverse=True)

    def finish(ver, rel, asset):
        found = {"version": ver, "vars": {"tag": rel["tag_name"]}}
        if asset:
            found["url"] = asset["browser_download_url"]
            digest = asset.get("digest") or ""
            if digest.startswith("sha256:"):
                found["sha256"], found["where"] = digest[7:], "GitHub asset digest"
        if "url" in rule:
            found["url"] = expand(rule["url"], ver, found["vars"])
        return found

    for ver, rel in cands:
        if "asset" not in rule:
            if in_track(ver, rule):
                return finish(ver, rel, None)
            continue
        hits = [(m, a) for a in rel["assets"] if (m := re.search(rule["asset"], a["name"]))]
        if hits and hits[0][0].groups():
            # The version is in the asset name (a date-tagged release holds many).
            hits = [(m, a) for m, a in hits if in_track(m.group(1), rule)]
            if hits:
                m, a = max(hits, key=lambda h: nums(h[0].group(1)))
                return finish(m.group(1), rel, a)
            continue
        if not in_track(ver, rule):
            continue
        if not hits:
            raise Skip(f"release {rel['tag_name']} of {rule['repo']} has no asset matching {rule['asset']!r}")
        return finish(ver, rel, hits[0][1])
    raise Skip(f"no release of {rule['repo']} in track {rule.get('track')}")


_apt = {}


def find_apt(rule):
    url = rule["packages"]
    if url not in _apt:
        best = {}
        for para in page(url).split("\n\n"):
            f = dict(l.split(": ", 1) for l in para.splitlines() if ": " in l and not l.startswith(" "))
            name = f.get("Package")
            if name and (name not in best or deb_cmp(f["Version"], best[name]["Version"]) > 0):
                best[name] = f
        _apt[url] = best
    f = _apt[url].get(rule["package"])
    if not f:
        raise Skip(f"{rule['package']} is not in {url}")
    base = rule.get("base") or url.split("/dists/")[0] + "/"
    host = urllib.parse.urlsplit(url).hostname
    return {"version": f["Version"], "url": urllib.parse.urljoin(base, f["Filename"]),
            "sha256": f["SHA256"], "where": f"SHA256 in the Packages index of {host}", "vars": {}}


def find_regex(rule):
    extra = {}
    if "dir_page" in rule:
        dirs = set(re.findall(rule["dir_regex"], page(rule["dir_page"])))
        if not dirs:
            raise Skip(f"nothing at {rule['dir_page']} matches {rule['dir_regex']!r}")
        extra["dir"] = max(dirs, key=nums)
    url = expand(rule["page"], "", extra)
    found = [v for v in set(re.findall(rule["regex"], page(url))) if in_track(v, rule)]
    if not found:
        raise Skip(f"nothing at {url} matches {rule['regex']!r}")
    ver = max(found, key=nums)
    return {"version": ver, "url": expand(rule["url"], ver, extra), "vars": extra}


def find_redirect(rule):
    url = final_url(rule["page"])
    m = re.search(rule["regex"], url)
    if not m:
        raise Skip(f"{rule['page']} led to {url}, which does not match {rule['regex']!r}")
    return {"version": m.group(1), "url": url, "vars": {}}


def jget(doc, path):
    for k in path.split("."):
        doc = doc[int(k)] if isinstance(doc, list) else doc[k]
    return doc


def find_json(rule):
    doc = json.loads(page(rule["page"]))
    ver = str(jget(doc, rule["version_key"]))
    found = {"version": ver, "vars": {}}
    found["url"] = jget(doc, rule["url_key"]) if "url_key" in rule else expand(rule["url"], ver, {})
    if "sha256_key" in rule:
        found["sha256"] = jget(doc, rule["sha256_key"])
        found["where"] = "sha256 published by " + urllib.parse.urlsplit(rule["page"]).hostname
    if "sums_key" in rule:
        found["sums"] = jget(doc, rule["sums_key"])
    return found


def find_node(rule):
    lts = [e for e in json.loads(page("https://nodejs.org/dist/index.json"))
           if e.get("lts") and in_track(e["version"], rule)]
    if not lts:
        raise Skip("no LTS release of Node in track")
    v = max(lts, key=lambda e: nums(e["version"]))["version"].lstrip("v")
    return {"version": v, "vars": {},
            "url": f"https://nodejs.org/dist/v{v}/node-v{v}-linux-x64.tar.xz",
            "sums": f"https://nodejs.org/dist/v{v}/SHASUMS256.txt"}


def find_dotnet(rule):
    index = json.loads(page("https://builds.dotnet.microsoft.com/dotnet/release-metadata/releases-index.json"))
    chans = index["releases-index"]
    if "track" in rule:
        chans = [c for c in chans if c["channel-version"] == rule["track"]]
    else:
        chans = [c for c in chans if c["release-type"] == "lts" and c["support-phase"] == "active"]
    if not chans:
        raise Skip("no active LTS channel of .NET")
    v = max(chans, key=lambda c: nums(c["channel-version"]))["latest-sdk"]
    url = f"https://builds.dotnet.microsoft.com/dotnet/Sdk/{v}/dotnet-sdk-{v}-linux-x64.tar.gz"
    return {"version": v, "url": url, "sums": url + ".sha512", "vars": {}}


FINDERS = {"github": find_github, "apt": find_apt, "regex": find_regex, "redirect": find_redirect,
           "json": find_json, "node": find_node, "dotnet": find_dotnet}


# -- the recipe --------------------------------------------------------------

def verify(sha256, sha512, found, rule):
    """Check the download against what upstream publishes; the provenance."""
    if found.get("sha256"):
        if found["sha256"].lower() != sha256:
            raise Skip(f"sha256 of {found['url']} does not match the {found['where']}")
        return found["where"]
    sums = found.get("sums")
    if not sums and "sums" in rule:
        sums = expand(rule["sums"], found["version"], {**found["vars"], "url": found["url"]})
    if sums:
        if sha256 not in page(sums) and sha512 not in page(sums):
            raise Skip(f"hash of {found['url']} is not listed at {sums}")
        return "listed at " + sums
    return "none published; sha256 of the download"


def rewrite(text, old_version, new_version, release, moves):
    """moves: [(old_url, new_url, sha256)]; the recipe text with them applied."""
    lines = text.split("\n")
    for old_url, new_url, sha in moves:
        i = next(k for k, l in enumerate(lines) if re.match(r'\s*url\s*=\s*"' + re.escape(old_url) + '"', l))
        lines[i] = lines[i].replace(old_url, new_url)
        j = next(k for k in range(i + 1, len(lines)) if re.match(r"\s*sha256\s*=", lines[k]))
        lines[j] = re.sub(r'"[0-9A-Fa-f]*"', f'"{sha}"', lines[j])
    text = "\n".join(lines)
    for old_url, new_url, _ in moves:
        old_name, new_name = (urllib.parse.unquote(u.rsplit("/", 1)[-1]) for u in (old_url, new_url))
        if old_name != new_name:
            text = text.replace(old_name, new_name)
    if new_version != old_version:
        text = re.sub(r'(?m)^(version\s*=\s*)"[^"]*"', rf'\g<1>"{new_version}"', text, count=1)
    return re.sub(r"(?m)^(release\s*=\s*)\d+", rf"\g<1>{release}", text, count=1)


def process(rdir, write, dl):
    """One recipe directory; the table row (recipe, current, newest, action, checksum)."""
    recipe_path = os.path.join(rdir, "recipe.toml")
    with open(recipe_path) as f:
        text = f.read()
    recipe = tomllib.loads(text)
    with open(os.path.join(rdir, "update.toml"), "rb") as f:
        rules = tomllib.load(f).get("source", [])
    pkg = recipe["package"]
    name = f"{pkg['organization']}/{pkg['name']}"
    current, release = pkg["version"], pkg["release"]
    sources = recipe["source"]
    newest, moves = current, []
    try:
        for pos, rule in enumerate(rules, 1):
            idx = rule.get("source", pos)
            src = sources[idx - 1]
            found = FINDERS[rule["kind"]](rule)
            if not in_track(found["version"], rule):
                raise Skip(f"{found['version']} is outside track {rule['track']}")
            if idx == 1:
                newest = apm_version(found["version"], rule)
                if nums(newest) < nums(current):
                    raise Skip(f"upstream {found['version']} is behind the recipe's {current}")
            if urllib.parse.unquote(found["url"]) == urllib.parse.unquote(src["url"]):
                continue
            if idx == 1 and newest == current and rule.get("rebuild", True) is False:
                continue  # the same version built again elsewhere: not taken
            moves.append((src, found, rule))
        if not moves:
            return name, current, newest, "up to date", ""
        new_release = 1 if newest != current else release + 1
        action = f"bump to {newest}" if newest != current else f"sources moved, release {new_release}"
        if not write:
            return name, current, newest, action + " (dry run)", ""
        applied, provenance = [], []
        for src, found, rule in moves:
            path, s256, s512 = download(found["url"], dl)
            provenance.append(verify(s256, s512, found, rule))
            applied.append((src["url"], found["url"], s256))
        with open(recipe_path, "w") as f:
            f.write(rewrite(text, current, newest, new_release, applied))
        return name, current, newest, action, "; ".join(sorted(set(provenance)))
    except Skip as e:
        return name, current, newest, f"skip: {e}", ""
    except Exception as e:  # a vendor down, a parse failure: this recipe only
        return name, current, newest, f"skip: {type(e).__name__}: {e}", ""


def selftest():
    assert apm_version("156.0", {}) == "156.0.0"
    assert apm_version("26.08.1", {}) == "26.8.1"
    assert apm_version("1.135.06055", {}) == "1.135.6055"
    assert apm_version("1.8.2.5", {}) == "1.8.2"
    assert apm_version("1:1.3.4.258.gc60b10b", {}) == "1.3.4"
    assert apm_version("jdk-21.0.12.1+1-LTS", {}) == "21.0.12"
    assert apm_version("155.0.8059.39-1", {"version_from": r"^(\d+)\.0\.(\d+)\.(\d+)"}) == "155.8059.39"
    assert apm_version("1.0.0.87", {"version_from": r"^(\d+)\.(\d+)\.0\.(\d+)$"}) == "1.0.87"
    assert in_track("3.13.16", {"track": "3.13"}) and not in_track("3.14.0", {"track": "3.13"})
    assert deb_cmp("1:1.2.1-1.2+b2", "1.9.0-1") > 0
    assert deb_cmp("1.21.3-5+deb13u1", "1.21.3-5") > 0
    assert deb_cmp("1.0~rc1", "1.0") < 0
    assert deb_cmp("8.30.0", "8.30.0") == 0 and deb_cmp("5.12.0-beta.2", "5.11.0") > 0
    assert expand("x/{v1}.{v2}/f-{version}-{dir}", "26.08.1", {"dir": "d"}) == "x/26.08/f-26.08.1-d"
    text = ('[package]\nversion      = "4.0.1"\nrelease      = 3\n\n[[source]]\n'
            'url    = "https://x/A-4.0.1/a-4.0.1.AppImage"\nsha256 = "00"\n\n[build]\n'
            "prepare = '''\n./a-4.0.1.AppImage --appimage-extract\n'''\n")
    out = rewrite(text, "4.0.1", "4.1.0", 1, [("https://x/A-4.0.1/a-4.0.1.AppImage", "https://x/A-4.1.0/a-4.1.0.AppImage", "ff")])
    assert 'version      = "4.1.0"' in out and "release      = 1" in out
    assert 'url    = "https://x/A-4.1.0/a-4.1.0.AppImage"' in out and 'sha256 = "ff"' in out
    assert "./a-4.1.0.AppImage --appimage-extract" in out and "4.0.1" not in out
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser(description="bump the recipes to the newest upstream versions")
    ap.add_argument("--write", action="store_true", help="rewrite the recipes (default: dry run)")
    ap.add_argument("--only", action="append", default=[], metavar="ORG/NAME")
    ap.add_argument("--dl", default=os.path.join(tempfile.gettempdir(), "apm-bump"), help="download directory")
    ap.add_argument("--selftest", action="store_true")
    # One script for both indexes: apm-recipes has the same layout, so it is
    # pointed at rather than given a copy that would drift.
    ap.add_argument("--root", default=HERE, help="the index repository whose recipes/ to bump")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    dirs = sorted(d for d, _, files in os.walk(os.path.join(args.root, "recipes")) if "recipe.toml" in files)
    without = [d for d in dirs if not os.path.exists(os.path.join(d, "update.toml"))]
    print("| recipe | current | newest | action | checksum |")
    print("|---|---|---|---|---|")
    for d in dirs:
        if d in without:
            continue
        org_name = os.path.basename(d).replace(".", "/", 1)
        if args.only and org_name not in args.only:
            continue
        print("| " + " | ".join(process(d, args.write, args.dl)) + " |", flush=True)
    if without:
        print(f"\n{len(without)} recipe(s) without an update.toml, left alone: "
              + ", ".join(os.path.basename(d) for d in without), file=sys.stderr)


if __name__ == "__main__":
    main()
