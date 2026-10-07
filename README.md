# apm-thirdparty

**Packages here are not part of AOS.** They are compatibility software —
toolkits and applications written for other systems, carried over so
that the things people expect to run, run. They may not work, and an
update to them or to the OS may break them. That is the deal, and apm says
so when you add this repository.

The OS's own packages live in [apm-recipes](https://github.com/Jaxilian/apm-recipes).
A third-party package may depend on official ones; never the reverse.

## Use it

```sh
sudo apm repo add thirdparty https://github.com/Jaxilian/apm-thirdparty/releases/download/index --third-party
sudo apm update
apm find code          # marked [third-party]
sudo apm install vscode
```

The key is the same as the official repository's; trusting it once covers
both.

## What goes here

The GTK3 runtime and what runs on it; Electron applications (VS Code,
Discord, Spotify); Firefox and Chrome; Steam and Lutris; the creative
tools people ask for (Blender, Krita, Inkscape); and the Python runtime
Lutris runs on. Vendor binaries are never mirrored: a recipe points at
the vendor's own download and pins its checksum.

Chrome, Spotify, Blender, Krita, Inkscape and Lutris fetch `.deb` files
or AppImages, which apm reads from v0.1.13 on, so they need an AOS
release with that apm (0.3.0) to build; on 0.2.9 they are listed and
fail. Lutris runs on `runtime/python`, built by `runtime-python.sh` in
the OS tree. `apps-test.py` installs each of them on the live ISO in QEMU
and screendumps it.

Layout, publishing and the rules for a recipe are as in
[apm-recipes](https://github.com/Jaxilian/apm-recipes#layout).
