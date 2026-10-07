#!/usr/bin/env python3
"""The new third-party apps on the live ISO in QEMU, one at a time:
apm (the working tree's, which reads .deb and AppImage sources) builds
each recipe in the guest from the vendor's own download, installs it,
the app is started in admin's session, and after a while it must still
be running and have drawn a window (a screendump before and after).
Then it is removed, so the live tmpfs never holds two.

Runs beside other QEMU drivers: its own copy of the ISO, its own
sockets and port, under ~/Projects/OS/scratch/apps-vm. The recipes come
from a local signed index of recipes/ (publish.sh's own steps), the GTK3
runtime from the public third-party index, served to the guest over
HTTP from the host (10.0.2.2 in QEMU's user network).

    python3 apps-test.py [name ...]     default: every new app
    APKG_DIR=dir python3 apps-test.py lutris
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OS_DIR = os.path.dirname(HERE)
AOS = os.path.join(OS_DIR, "aos")
APM = os.path.join(OS_DIR, "apm", "target", "release", "apm")
WORK = os.path.join(OS_DIR, "scratch", "apps-vm")
HTTP_PORT = 8731

NAMES = sys.argv[1:]
sys.argv = ["boot-test.py", "desktop"]
spec = importlib.util.spec_from_file_location("bt", os.path.join(AOS, "br2ext", "board", "aos", "boot-test.py"))
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)

# What each app is started as, and the process name that must be alive.
APPS = {
    "chrome":   ("google-chrome", "chrome"),
    "spotify":  ("spotify", "spotify"),
    "blender":  ("blender", "blender"),
    "krita":    ("krita", "krita"),
    "inkscape": ("inkscape", "inkscape"),
    "lutris":   ("lutris", "lutris"),
}
ENV = ("WAYLAND_DISPLAY=wayland-1 XDG_RUNTIME_DIR=/run/user/1000 HOME=/home/admin "
       "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus")


def private_vm():
    """Point the harness at a directory of our own, with a copy of the ISO."""
    os.makedirs(WORK, exist_ok=True)
    iso = os.path.join(WORK, "rootfs.iso9660")
    shutil.copy(bt.ISO, iso)
    bt.OUT = WORK
    bt.ISO = iso
    bt.SER = os.path.join(WORK, "serial.sock")
    bt.MON = os.path.join(WORK, "monitor.sock")
    bt.VARS = os.path.join(WORK, "OVMF_VARS.fd")
    bt.STICK = os.path.join(WORK, "stick.img")
    bt.SSH_PORT = 2232
    for p in (bt.SER, bt.MON):
        if os.path.exists(p):
            os.unlink(p)
    shutil.copy(bt.OVMF_VARS, bt.VARS)


def local_index():
    """recipes/ and icons/ signed into WORK/www/index, as publish.sh does."""
    www = os.path.join(WORK, "www")
    idx = os.path.join(www, "index")
    shutil.rmtree(www, ignore_errors=True)
    os.makedirs(idx)
    # recipes/ is what publish.sh ships; staging/ holds the apps that wait
    # for an OS release with the apm they need.
    for recipes in (os.path.join(HERE, "recipes"), os.path.join(HERE, "staging", "recipes")):
        for shard in os.listdir(recipes):
            for pkg in os.listdir(os.path.join(recipes, shard)):
                if pkg.split(".", 1)[1] not in APPS:
                    continue  # the published apps come from the public index
                shutil.copy(os.path.join(recipes, shard, pkg, "recipe.toml"),
                            os.path.join(idx, pkg + ".recipe.toml"))
    # Prebuilt packages not yet published (a new runtime): APKG_DIR=dir.
    extra = os.environ.get("APKG_DIR")
    for apkg in (os.listdir(extra) if extra else []):
        if apkg.endswith(".apkg"):
            shutil.copy(os.path.join(extra, apkg), idx)
    for icons in (os.path.join(HERE, "icons"), os.path.join(HERE, "staging", "icons")):
        for icon in os.listdir(icons):
            shutil.copy(os.path.join(icons, icon), idx)
    subprocess.run([APM, "index", idx, "--sign"], check=True, stdout=subprocess.DEVNULL)
    shutil.copy(APM, os.path.join(www, "apm"))
    return www


def main():
    names = NAMES or list(APPS)
    private_vm()
    www = local_index()
    http = subprocess.Popen([sys.executable, "-m", "http.server", str(HTTP_PORT), "--bind", "127.0.0.1"],
                            cwd=www, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cmd = bt.qemu_command()
    cmd[cmd.index("-m") + 1] = "10G"
    # QEMU's own words (a watchdog reset, a guest crash) go to qemu.log.
    qemu_log = open(os.path.join(WORK, "qemu.log"), "w")
    q = subprocess.Popen(cmd, stdout=qemu_log, stderr=subprocess.STDOUT)
    results = {}
    try:
        ser = bt.Serial(bt.SER, os.path.join(WORK, "apps.serial.txt"))
        if ser.read_until(b"login:", bt.LOGIN_TIMEOUT) is None:
            print("!! no login prompt")
            return False
        ser.send("root\n")
        ser.read_until(b"# ", 30)
        # Echo must be off: an echoed command line carries the completion
        # marker, and `run` would return it with nothing in it. The shell
        # is not always ready for the first stty, so prove it with a value
        # the echoed line cannot contain and try again until it holds.
        for _ in range(6):
            ser.send("stty -echo cols 200 rows 50; export SYSTEMD_PAGER= PAGER=cat\n")
            ser.read_until(b"# ", 10)
            ser.read_until(b"__drain__", 1)
            if "42" in ser.run("echo $((6*7))"):
                break
            ser.read_until(b"__drain__", 2)
        else:
            print("!! the serial shell keeps echoing; aborting")
            return False
        for _ in range(60):
            if "ade-comp" in ser.run("pgrep -x ade-comp >/dev/null && echo ade-comp"):
                break
            time.sleep(2)
        time.sleep(10)
        setup = ser.run(
            "systemctl stop aos-update-check.timer aos-update-check.service 2>/dev/null; "
            "mkdir -p /aos/t; curl -sf -o /aos/t/apm http://10.0.2.2:%d/apm && chmod 755 /aos/t/apm && /aos/t/apm help | head -1; "
            "mkdir -p /aos/t/index && cd /aos/t/index && "
            "for f in $(curl -sf http://10.0.2.2:%d/index/ | sed -n 's/.*href=\"\\([^\"]*\\)\".*/\\1/p'); do curl -sf -O http://10.0.2.2:%d/index/$f; done; cd /; "
            "/aos/t/apm repo add thirdparty https://github.com/Jaxilian/apm-thirdparty/releases/download/index --third-party 2>&1 | tail -1; "
            "/aos/t/apm repo add newapps file:///aos/t/index --third-party 2>&1 | tail -1; "
            "/aos/t/apm update --force 2>&1; df -h /aos | tail -1"
            % (HTTP_PORT, HTTP_PORT, HTTP_PORT), timeout=300)
        print("$ setup\n%s" % setup)
        updated = lambda text, repo: any(line.startswith(repo) and "updated" in line for line in text.split("\n"))
        if not all(updated(setup, r) for r in ("main", "thirdparty", "newapps")):
            setup = ser.run("/aos/t/apm update --force 2>&1", timeout=300)
            print("$ update again\n%s" % setup)
        for repo in ("main", "thirdparty", "newapps"):
            if not updated(setup, repo):
                print("!! repository %s was not updated; aborting" % repo)
                return False
        before = bt.shot("apps-before")
        for name in names:
            command, proc = APPS[name]
            print("\n==== %s" % name)
            out = ser.run("/aos/t/apm --yes --quiet install %s 2>&1 | tail -4; ls /opt/apm/bin/%s" % (name, command), timeout=1800)
            print(out)
            if "No such file" in out:
                results[name] = "install failed"
                continue
            ser.run("printf '#!/bin/sh\\nexec >/tmp/%s.log 2>&1\\nexec /opt/apm/bin/%s\\n' > /tmp/run-%s.sh; chmod 755 /tmp/run-%s.sh; "
                    "su -s /bin/sh admin -c 'export %s; setsid /tmp/run-%s.sh &' </dev/null"
                    % (name, command, name, name, ENV.replace(" ", "; export "), name))
            # 45 s in nine steps, the log's tail each time: when the VM
            # dies with the app, the serial transcript still has the last
            # thing it said.
            for _ in range(9):
                time.sleep(5)
                print(ser.run("tail -2 /tmp/%s.log | cut -c1-160; dmesg | tail -1 | cut -c1-160" % name).replace("virtio_gpu: driver missing", "").strip())
            alive = ser.run("pgrep -f '%s' >/dev/null && echo ALIVE || echo DEAD" % proc)
            log = ser.run("cut -c1-180 /tmp/%s.log | grep -v 'virtio_gpu: driver missing' | tail -%d" % (name, 15 if "ALIVE" in alive else 80))
            shot = bt.shot("apps-%s" % name)
            # Below the bar: its clock changes the top rows on its own.
            # The VGA output is 1280x800 RGB; the bar is the top 30 rows.
            bar = 30 * 1280 * 3
            changed = shot is not None and before is not None and shot[1][bar:] != before[1][bar:]
            print("%s\n-- log tail:\n%s" % (alive.strip(), log))
            results[name] = "ok" if "ALIVE" in alive and changed else ("running, screen unchanged" if "ALIVE" in alive else "died")
            ser.run("for p in $(pgrep -u admin); do kill $p 2>/dev/null; done; sleep 3; "
                    "for p in $(pgrep -u admin); do kill -9 $p 2>/dev/null; done; true")
            time.sleep(3)
            ser.run("/aos/t/apm --yes --force remove %s >/dev/null 2>&1; rm -rf /opt/apm/cache/build /opt/apm/cache/downloads; df -h /aos | tail -1" % name, timeout=300)
        ser.send("poweroff\n")
        ser.read_until(b"reboot: Power down", 90)
    finally:
        http.terminate()
        try:
            bt.monitor("quit")
        except OSError:
            pass
        try:
            q.wait(15)
        except subprocess.TimeoutExpired:
            q.kill()
    print("\n==== results")
    for name, r in results.items():
        print("%-9s %s" % (name, r))
    return bool(results) and all(r == "ok" for r in results.values())


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
