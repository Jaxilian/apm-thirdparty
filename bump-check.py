#!/usr/bin/env python3
"""The last gate before a bump is published: the bumped builds are
installed on the installed AOS disk in QEMU, together with every recipe
of the two indexes that depends on them, and each app among them is
started in its real sandbox by aos-launch-check. A FAIL line fails the
package. auto-bump.sh runs this when BUMP_QEMU=1 and publishes only what
passed.

    python3 -I bump-check.py BUILD.apkg...        the bumped builds
    BUMP_DEPENDENTS=0                             only the bumped ones
    AOS_OUT=/path/to/output3                      the build (boot-test.py's)
    AOS_CHECK_WAIT=20                             seconds an app must stay up

Boots the disk boot-test.py's `install` left (AOS_OUT/images/aos-disk.img)
through drv.boot, so the compositor and the session are the real ones. The
builds reach the guest the way apps-test.py's do, over HTTP from the host
(10.0.2.2 in QEMU's user network) into a local signed index, so no SSH key
and no keyed build is needed: the release build left behind works.
The apm under test is the host's (../apm/target/release/apm), run through
a wrapper whose os-release says 9.9.9 so a recipe's min_aos never holds
it on a development build.

Results: one line per package on stdout, `ok ORG/NAME` or `fail ORG/NAME
why`, and the same in $BUMP_RESULTS (default: beside the QEMU work files,
bump-check.txt) for auto-bump.sh. Exit 0 when every package passed.
Serial transcript: <AOS_OUT>/images/bump.serial.txt; screendumps bump-*.png.
"""
import os
import shutil
import subprocess
import sys
import tomllib
import zipfile

sys.path.insert(0, "/home/jax/Projects/OS/aos/br2ext/board/aos")
import drv  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OS_DIR = os.path.dirname(HERE)
APM = os.environ.get("APM", os.path.join(OS_DIR, "apm", "target", "release", "apm"))
REPOS = [os.path.join(OS_DIR, "apm-thirdparty"), os.path.join(OS_DIR, "apm-recipes")]
HTTP_PORT = int(os.environ.get("BUMP_HTTP_PORT", "8732"))
GUEST_APM = "/aos/t/apm"


def manifest_of(apkg):
    with zipfile.ZipFile(apkg) as z:
        return tomllib.loads(z.read("manifest.toml").decode())


def package_id(manifest):
    p = manifest["package"]
    return "%s/%s" % (p["organization"], p["name"])


def dependents(ids):
    """Every recipe in the two indexes with a [[dependencies]] on one of ids:
    (org/name, whether it has a launcher)."""
    found = []
    for repo in REPOS:
        recipes = os.path.join(repo, "recipes")
        if not os.path.isdir(recipes):
            continue
        for shard in sorted(os.listdir(recipes)):
            for pkg in sorted(os.listdir(os.path.join(recipes, shard))):
                path = os.path.join(recipes, shard, pkg, "recipe.toml")
                if not os.path.isfile(path):
                    continue
                with open(path, "rb") as f:
                    r = tomllib.load(f)
                deps = ["%s/%s" % (d["organization"], d["name"]) for d in r.get("dependencies", [])]
                if any(d in ids for d in deps):
                    rid = package_id(r)
                    if rid not in ids:
                        found.append((rid, "launcher" in r))
    return found


def local_index(work, apkgs):
    """The bumped builds signed into work/www/index with the host's key."""
    www = os.path.join(work, "www")
    idx = os.path.join(www, "index")
    shutil.rmtree(www, ignore_errors=True)
    os.makedirs(idx)
    for a in apkgs:
        shutil.copy(a, idx)
    subprocess.run([APM, "index", idx, "--sign"], check=True, stdout=subprocess.DEVNULL)
    shutil.copy(APM, os.path.join(www, "apm"))
    return www


def main(apkgs):
    if not apkgs:
        print(__doc__)
        return False
    if not os.path.exists(APM):
        print("!! no apm at %s (cargo build --release in ../apm)" % APM)
        return False
    if not os.path.exists(drv.bt.DISK):
        print("!! no installed disk at %s (boot-test.py install first)" % drv.bt.DISK)
        return False
    work = os.path.join(drv.bt.OUT, "bump-vm")
    os.makedirs(work, exist_ok=True)
    results_path = os.environ.get("BUMP_RESULTS", os.path.join(work, "bump-check.txt"))

    bumped = {}
    for a in apkgs:
        m = manifest_of(a)
        bumped[package_id(m)] = "launcher" in m
    ids = list(bumped)
    extra = dependents(ids) if os.environ.get("BUMP_DEPENDENTS", "1") != "0" else []
    print("== bumped: %s" % " ".join(ids))
    print("== dependents: %s" % (" ".join(i for i, _ in extra) or "none"))
    to_install = ids + [i for i, _ in extra]
    apps = [i for i, has in list(bumped.items()) + extra if has]

    www = local_index(work, apkgs)
    http = subprocess.Popen([sys.executable, "-m", "http.server", str(HTTP_PORT), "--bind", "127.0.0.1"],
                            cwd=www, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    results = {}
    vm = None
    try:
        vm = drv.boot("bump")
        ser = vm.ser
        setup = ser.run(
            "systemctl stop aos-update-check.timer aos-update-check.service 2>/dev/null; "
            "mkdir -p /aos/t/index && curl -sf -o /aos/t/apm.bin http://10.0.2.2:%d/apm && chmod 755 /aos/t/apm.bin && "
            "sed 's/^VERSION_ID=.*/VERSION_ID=9.9.9/' /usr/lib/os-release > /aos/t/os-release && "
            "printf '#!/bin/sh\\nexport APM_OS_RELEASE=/aos/t/os-release\\nexec /aos/t/apm.bin \"$@\"\\n' > %s && chmod 755 %s && %s --version; "
            "cd /aos/t/index && for f in $(curl -sf http://10.0.2.2:%d/index/ | sed -n 's/.*href=\"\\([^\"]*\\)\".*/\\1/p'); do curl -sf -O http://10.0.2.2:%d/index/$f; done; cd /; "
            "%s repo add thirdparty https://github.com/Jaxilian/apm-thirdparty/releases/download/index --third-party 2>&1 | tail -1; "
            "%s repo add bump file:///aos/t/index --third-party 2>&1 | tail -1; "
            "for i in $(seq 30); do getent hosts github.com >/dev/null 2>&1 && break; sleep 1; done; "
            "%s update --force 2>&1 | tail -4"
            % (HTTP_PORT, GUEST_APM, GUEST_APM, GUEST_APM, HTTP_PORT, HTTP_PORT, GUEST_APM, GUEST_APM, GUEST_APM), timeout=300)
        print("$ setup\n%s" % setup)
        if "bump" not in setup or "updated" not in setup:
            print("!! the local index was not taken; see the setup output")
            for i in to_install:
                results[i] = "fail setup: the local index was not taken"
            return False

        installed = []
        for pkg in to_install:
            # A dependent is a recipe the guest builds from the vendor's
            # download through slirp: give it time.
            out = ser.run("%s install %s --quiet --yes >/tmp/bump.log 2>&1; echo rc=$?; tail -3 /tmp/bump.log"
                          % (GUEST_APM, pkg), timeout=2400)
            print("$ install %s\n%s" % (pkg, out))
            if "rc=0" in out or "already installed" in out:
                installed.append(pkg)
            else:
                results[pkg] = "fail install: %s" % out.replace("# ", "").strip().split("\n")[-1]
        drv.shot("bump-installed")

        to_check = [a for a in apps if a in installed]
        if to_check:
            wait = os.environ.get("AOS_CHECK_WAIT", "20")
            cmd = "AOS_CHECK_WAIT=%s aos-launch-check %s 2>&1" % (wait, " ".join(to_check))
            out = ser.run(drv.as_user(cmd, vm), timeout=120 + (int(wait) + 30) * len(to_check))
            print("$ aos-launch-check\n%s" % out)
            drv.shot("bump-launched")
            for line in out.replace("# ", "").split("\n"):
                words = line.split()
                if len(words) >= 2 and words[0] in ("ok", "FAIL") and words[1].rstrip(":") in to_check:
                    pkg = words[1].rstrip(":")
                    results[pkg] = "ok" if words[0] == "ok" else "fail launch: %s" % " ".join(words[2:])
            for a in to_check:
                if a not in results:
                    results[a] = "fail launch: no verdict from aos-launch-check"
        for pkg in installed:
            if pkg not in results:
                results[pkg] = "ok"  # a library or a tool: installed, nothing to start

        # The disk is shared with other drivers: leave it as it was.
        for pkg in reversed(installed):
            ser.run("%s remove %s --force --quiet --yes 2>&1 | tail -1" % (GUEST_APM, pkg), timeout=300)
        ser.run("%s repo remove bump >/dev/null 2>&1; rm -rf /aos/t" % GUEST_APM)
    finally:
        if vm is not None:
            drv.stop(vm)
        http.terminate()
        # A dependent that fails is reported under the bumped build: that
        # build is what auto-bump.sh can put back.
        for pkg, _ in extra:
            if results.get(pkg, "fail no result") != "ok":
                for b in ids:
                    if results.get(b) == "ok":
                        results[b] = "fail dependent %s: %s" % (pkg, results.get(pkg, "no result"))
        lines = []
        for pkg in to_install:
            word, _, why = results.get(pkg, "fail no result").partition(" ")
            lines.append("%s %s%s" % (word, pkg, " " + why if why else ""))
        with open(results_path, "w") as f:
            f.write("".join(l + "\n" for l in lines if l.split()[1] in ids))
        print("\n".join(lines))
        print("== results in %s" % results_path)
    return all(results.get(b) == "ok" for b in ids)


if __name__ == "__main__":
    sys.exit(0 if drv.done(main(sys.argv[1:])) else 1)
