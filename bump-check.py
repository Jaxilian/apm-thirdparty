#!/usr/bin/env python3
"""The last gate before a bump is published: the bumped builds are
installed on the installed AOS disk in QEMU, together with every recipe
of the two indexes that depends on them, and each app among them is
started in its real sandbox by aos-launch-check. A FAIL line fails the
package. auto-bump.sh runs this when BUMP_QEMU=1 and publishes only what
passed.

    python3 -I bump-check.py BUILD.apkg...        the bumped builds
    python3 -I bump-check.py --selftest           the checks of the pure parts
    BUMP_DEPENDENTS=0                             only the bumped ones
    AOS_OUT=/path/to/output3                      the build (boot-test.py's)
    AOS_CHECK_WAIT=20                             seconds an app must stay up
    BUMP_APP_WINDOW=90                            seconds one app's check may take

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
import time
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
SANDBOX = os.environ.get("AOS_SANDBOX", os.path.join(OS_DIR, "aos", "br2ext", "board", "aos", "rootfs-overlay", "usr", "bin", "aos-sandbox"))
GUEST_SANDBOX = "/aos/t/aos-sandbox"
GUEST_REPO = "dev"
STORE = "/opt/apm/packages"
LAUNCH_LOGS = "/run/user/1000/aos-launch-check"
DARK = 0.01


def manifest_of(apkg):
    with zipfile.ZipFile(apkg) as z:
        return tomllib.loads(z.read("manifest.toml").decode())


def package_id(manifest):
    p = manifest["package"]
    return "%s/%s" % (p["organization"], p["name"])


def build_dir(manifest):
    p = manifest["package"]
    if manifest.get("manifest_version", 1) >= 2:
        return "%s+%s" % (p["version"], p["release"])
    return str(p["version"])


def dependents(ids):
    """Every recipe in the two indexes with a [[dependencies]] on one of ids:
    (org/name, whether it has a launcher, its path, the ids it depends on)."""
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
                on = [d for d in deps if d in ids]
                rid = package_id(r)
                if on and rid not in ids:
                    found.append((rid, "launcher" in r, path, on))
    return found


def local_index(work, apkgs, recipes):
    """The bumped builds and the dependents' recipes, as the tree has them,
    signed into work/www/index with the host's key."""
    www = os.path.join(work, "www")
    idx = os.path.join(www, "index")
    shutil.rmtree(www, ignore_errors=True)
    os.makedirs(idx)
    for a in apkgs:
        shutil.copy(a, idx)
    for path in recipes:
        shutil.copy(path, os.path.join(idx, os.path.basename(os.path.dirname(path)) + ".recipe.toml"))
    subprocess.run([APM, "index", idx, "--sign"], check=True, stdout=subprocess.DEVNULL)
    # `apm repo add dev URL` pins the key at URL/apm.pub: the host key's
    # public half, from where `apm index --sign` took the secret one
    # (~/AppData/aos.apm/etc/keys, apm-core sign.rs key_dir).
    pub = os.path.join(os.path.expanduser("~"), "AppData", "aos.apm", "etc", "keys", "apm.pub")
    if not os.path.exists(pub):
        pub = os.path.join(os.path.expanduser("~"), ".apm", "etc", "keys", "apm.pub")
    shutil.copy(pub, os.path.join(idx, "apm.pub"))
    shutil.copy(APM, os.path.join(www, "apm"))
    shutil.copy(SANDBOX, os.path.join(www, "aos-sandbox"))
    return www


def took_repo(setup, name):
    for line in setup.replace("# ", "").split("\n"):
        words = line.split()
        if len(words) >= 2 and words[0] == name and words[1].rstrip(",") == "updated":
            return True
    return False


def wrong_build(line, want):
    got = dict(w.split("=", 1) for w in line.replace("# ", "").split() if "=" in w)
    current = got.get("current", "")
    origin = got.get("origin", "")
    if current != want:
        return "installed %s, not the bumped %s" % (current or "nothing", want)
    if origin != GUEST_REPO:
        return "installed from %s, not from the local index" % (origin or "nowhere")
    return None


def verdict_of(text, app):
    for line in text.replace("# ", "").split("\n"):
        words = line.split()
        if len(words) < 2 or words[1].rstrip(":") != app:
            continue
        if words[0] == "ok":
            return "ok"
        if words[0] == "FAIL":
            return "fail launch: %s" % " ".join(words[2:])
    return None


def unverified(result):
    return "no verdict" in result or result == "fail no result"


def blame(results, extra, apps):
    out = dict(results)
    for pkg, _, _, on in extra:
        r = out.get(pkg, "fail no result")
        if r.startswith("ok"):
            continue
        why = r[5:] if r.startswith("fail ") else r
        for b in on:
            if not out.get(b, "").startswith("ok"):
                continue
            if not unverified(r):
                out[b] = "fail dependent %s: %s" % (pkg, why)
            elif b in apps:
                out[b] = "fail dependent %s not verified: %s" % (pkg, why)
            else:
                out[b] = "ok (dependent %s not verified: %s)" % (pkg, why)
    return out


def result_lines(results, order):
    lines = []
    for pkg in order:
        word, _, why = results.get(pkg, "fail no result").partition(" ")
        lines.append("%s %s%s" % (word, pkg, " " + why if why else ""))
    return lines


def no_blanking(vm):
    cmd = ("p=$(pidof ade-comp 2>/dev/null || pgrep -x ade-comp 2>/dev/null); p=${p%%%% *}; "
           "d=$(tr '\\0' '\\n' < /proc/$p/environ 2>/dev/null | sed -n 's/^XDG_CONFIG_HOME=//p'); "
           "[ -n \"$d\" ] || d=%s/AppData/aos.desktop/config; f=$d/ade/display; o=$(stat -c %%u:%%g %s); "
           "mkdir -p $d/ade && chown $o $d/ade; [ -f $f ] && cp -p $f $f.bump-saved; touch $f; "
           "sed -i '/^\\(blank\\|blank_ac\\|suspend\\|suspend_ac\\)=/d' $f; "
           "printf 'blank=0\\nblank_ac=0\\nsuspend=0\\nsuspend_ac=0\\n' >> $f; chown $o $f; echo display=$f"
           % (vm.home, vm.home))
    out = vm.ser.run(cmd, 60)
    print("$ no blanking\n%s" % out, flush=True)
    for word in out.replace("# ", "").split():
        if word.startswith("display="):
            return word[len("display="):]
    return None


def blanking_back(vm, path):
    if path:
        vm.ser.run("f=%s; if [ -f $f.bump-saved ]; then mv $f.bump-saved $f; else rm -f $f; fi" % path, 60)


def desktop_up(vm):
    drv.move(vm, 640, 400)
    drv.move(vm, 660, 420)
    time.sleep(2)
    ink = drv.ink("bump-desktop")
    if ink < DARK:
        print("!! the desktop is black before the launch step (%.1f%% ink)" % (ink * 100), flush=True)
        return False
    print("== the desktop is up before the launch step (%.1f%% ink)" % (ink * 100), flush=True)
    return True


def settle(vm, app):
    unit = "aos-launch-check-" + app.replace("/", ".")
    for _ in range(3):
        vm.ser.send("\x03")
        time.sleep(1)
        cmd = "systemctl --user kill -s KILL %s; systemctl --user stop %s; systemctl --user reset-failed %s" % (unit, unit, unit)
        if not vm.ser.run(drv.as_user(cmd + " 2>/dev/null; true", vm), 60).startswith("!! TIMEOUT"):
            return


def launch(vm, app, wait, window):
    cmd = drv.as_user("AOS_SANDBOX=%s AOS_CHECK_WAIT=%s aos-launch-check %s 2>&1" % (GUEST_SANDBOX, wait, app), vm)
    marker = "__BUMP_%d__" % int(time.time() * 1000)
    vm.ser.send("echo; %s; echo %s\n" % (cmd, marker))
    out = vm.ser.read_until(marker.encode(), window)
    late = out is None
    if late:
        out, vm.ser.buf = vm.ser.buf, b""
    text = drv.bt.unescape(out.decode(errors="replace")).replace("\r", "").replace(marker, "")
    print("$ aos-launch-check %s%s\n%s" % (app, " (past its %d s window)" % window if late else "", text.strip()), flush=True)
    if late:
        settle(vm, app)
    verdict = verdict_of(text, app)
    if verdict is None and late:
        return "fail launch: no verdict within %d s" % window
    if verdict is None:
        return "fail launch: no verdict from aos-launch-check"
    if verdict.startswith("fail"):
        log = drv.user(vm, "tail -n 40 %s/%s.log" % (LAUNCH_LOGS, app.replace("/", ".")), 60)
        print("\n".join("     | " + l for l in log), flush=True)
    return verdict


def check_build(vm, pkg, want):
    path = "%s/%s" % (STORE, pkg)
    out = vm.ser.run("echo current=$(readlink %s/current) origin=$(cut -d' ' -f1 %s/origin 2>/dev/null)" % (path, path), 60)
    line = [l for l in out.split("\n") if "current=" in l]
    return wrong_build(line[-1] if line else "", want)


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
    sys.stdout.reconfigure(line_buffering=True)
    work = os.path.join(drv.bt.OUT, "bump-vm")
    os.makedirs(work, exist_ok=True)
    results_path = os.environ.get("BUMP_RESULTS", os.path.join(work, "bump-check.txt"))

    bumped = {}
    builds = {}
    for a in apkgs:
        m = manifest_of(a)
        bumped[package_id(m)] = "launcher" in m
        builds[package_id(m)] = build_dir(m)
    ids = list(bumped)
    extra = dependents(ids) if os.environ.get("BUMP_DEPENDENTS", "1") != "0" else []
    print("== bumped: %s" % " ".join(ids))
    print("== dependents: %s" % (" ".join("%s (on %s)" % (i, ",".join(on)) for i, _, _, on in extra) or "none"))
    to_install = ids + [i for i, _, _, _ in extra]
    apps = [i for i, has in list(bumped.items()) + [(i, has) for i, has, _, _ in extra] if has]

    www = local_index(work, apkgs, [])
    http = subprocess.Popen([sys.executable, "-m", "http.server", str(HTTP_PORT), "--bind", "127.0.0.1"],
                            cwd=www, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    results = {}
    vm = None
    display = None
    try:
        vm = drv.boot("bump")
        ser = vm.ser
        display = no_blanking(vm)
        setup = ser.run(
            "systemctl stop aos-update-check.timer aos-update-check.service 2>/dev/null; "
            "mkdir -p /aos/t/index && curl -sf -o /aos/t/apm.bin http://10.0.2.2:%d/apm && chmod 755 /aos/t/apm.bin && "
            "curl -sf -o %s http://10.0.2.2:%d/aos-sandbox && chmod 755 %s && "
            "sed 's/^VERSION_ID=.*/VERSION_ID=9.9.9/' /usr/lib/os-release > /aos/t/os-release && "
            "printf '#!/bin/sh\\nexport APM_OS_RELEASE=/aos/t/os-release\\nexec /aos/t/apm.bin \"$@\"\\n' > %s && chmod 755 %s && %s --version; "
            "cd /aos/t/index && for f in $(curl -sf http://10.0.2.2:%d/index/ | sed -n 's/.*href=\"\\([^\"]*\\)\".*/\\1/p'); do curl -sf -O http://10.0.2.2:%d/index/$f; done; cd /; "
            "for i in $(seq 30); do getent hosts github.com >/dev/null 2>&1 && break; sleep 1; done; "
            "%s repo add thirdparty https://github.com/Jaxilian/apm-thirdparty/releases/download/index 2>&1 | tail -1; "
            "%s repo add %s file:///aos/t/index 2>&1 | tail -1; "
            "%s update --force 2>&1 | tail -4"
            % (HTTP_PORT, GUEST_SANDBOX, HTTP_PORT, GUEST_SANDBOX, GUEST_APM, GUEST_APM, GUEST_APM, HTTP_PORT, HTTP_PORT, GUEST_APM, GUEST_APM, GUEST_REPO, GUEST_APM), timeout=300)
        print("$ setup\n%s" % setup)
        if not took_repo(setup, GUEST_REPO):
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
            if "rc=0" not in out and "already installed" not in out:
                results[pkg] = "fail install: %s" % out.replace("# ", "").strip().split("\n")[-1]
                continue
            installed.append(pkg)
            wrong = check_build(vm, pkg, builds[pkg]) if pkg in builds else None
            if wrong:
                print("!! %s: %s" % (pkg, wrong))
                results[pkg] = "fail install: %s" % wrong
        drv.shot("bump-installed")

        to_check = [a for a in apps if a in installed and a not in results]
        if to_check:
            desktop_up(vm)
            wait = int(os.environ.get("AOS_CHECK_WAIT", "20"))
            window = int(os.environ.get("BUMP_APP_WINDOW", str(wait + 70)))
            for a in to_check:
                results[a] = launch(vm, a, wait, window)
                print("%s %s" % (results[a].partition(" ")[0], a), flush=True)
            drv.shot("bump-launched")
        for pkg in installed:
            if pkg not in results:
                results[pkg] = "ok"  # a library or a tool: installed, nothing to start

        # The disk is shared with other drivers: leave it as it was.
        for pkg in reversed(installed):
            ser.run("%s remove %s --force --quiet --yes 2>&1 | tail -1" % (GUEST_APM, pkg), timeout=300)
        # The closure "bad" marks too: a crash in this gate would otherwise
        # stay on the shared disk, and the same closure text hashes the same.
        ser.run("%s repo remove %s >/dev/null 2>&1; rm -rf /aos/t %s/.local/state/aos/apps" % (GUEST_APM, GUEST_REPO, vm.home))
        blanking_back(vm, display)
    finally:
        if vm is not None:
            drv.stop(vm)
        http.terminate()
        results = blame(results, extra, apps)
        lines = result_lines(results, to_install)
        with open(results_path, "w") as f:
            f.write("".join(l + "\n" for l in lines if l.split()[1] in ids))
        print("\n".join(lines))
        print("== results in %s" % results_path)
    return all(results.get(b, "").startswith("ok") for b in ids)


def selftest():
    assert build_dir({"manifest_version": 2, "package": {"version": "2026.6.0", "release": 1}}) == "2026.6.0+1"
    assert build_dir({"package": {"version": "1.0.0", "release": 3}}) == "1.0.0"
    assert wrong_build("# current=2026.6.0+1 origin=dev", "2026.6.0+1") is None
    assert wrong_build("current=2026.5.0+1 origin=main", "2026.6.0+1") == "installed 2026.5.0+1, not the bumped 2026.6.0+1"
    assert wrong_build("current=2026.6.0+1 origin=main", "2026.6.0+1") == "installed from main, not from the local index"
    assert wrong_build("current= origin=", "1.0.0+1") == "installed nothing, not the bumped 1.0.0+1"
    assert took_repo("main         updated, 101 packages, signed\ndev          updated, 4 packages, signed", "dev")
    assert not took_repo("main  updated, 101 packages\nbump  updated, 4 packages", "dev")
    assert verdict_of("# ok   brave/brave (up after 20s)\n", "brave/brave") == "ok"
    assert verdict_of("FAIL discord/discord: exited after 1s\n     | x", "discord/discord") == "fail launch: exited after 1s"
    assert verdict_of("ok   brave/brave (up after 20s)", "brave/brave-beta") is None
    assert verdict_of("", "brave/brave") is None
    extra = [("google/android-studio", True, "p", ["gradle/gradle"]), ("x/app", True, "q", ["a/lib", "b/app"])]
    apps = ["b/app", "google/android-studio", "x/app"]
    got = blame({"gradle/gradle": "ok", "gh/gh": "ok", "google/android-studio": "fail launch: no verdict within 90 s",
                 "a/lib": "ok", "b/app": "ok", "x/app": "fail launch: exited after 1s"}, extra, apps)
    assert got["gh/gh"] == "ok"
    assert got["gradle/gradle"] == "ok (dependent google/android-studio not verified: launch: no verdict within 90 s)"
    assert got["a/lib"] == "fail dependent x/app: launch: exited after 1s"
    assert got["b/app"] == "fail dependent x/app: launch: exited after 1s"
    got = blame({"b/app": "ok", "x/app": "fail launch: no verdict within 90 s", "a/lib": "ok"}, extra, apps)
    assert got["b/app"] == "fail dependent x/app not verified: launch: no verdict within 90 s"
    assert got["a/lib"].startswith("ok (dependent x/app not verified")
    got = blame({"b/app": "ok", "a/lib": "ok"}, extra, apps)
    assert got["b/app"] == "fail dependent x/app not verified: no result"
    lines = result_lines({"a/lib": "ok (dependent x/app not verified: no result)", "b/app": "ok"}, ["a/lib", "b/app", "c/c"])
    assert lines == ["ok a/lib (dependent x/app not verified: no result)", "ok b/app", "fail c/c no result"]
    print("selftest ok")
    return True


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(0 if selftest() else 1)
    sys.exit(0 if drv.done(main(sys.argv[1:])) else 1)
