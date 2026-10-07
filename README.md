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

`staging/` holds recipes that are written and tested but not published:
Chrome, Spotify, Blender, Krita, Inkscape and Lutris fetch `.deb` files
or AppImages, which apm reads from v0.1.13 on, so they go into the index
with the AOS release that carries that apm (`git mv staging/recipes/<xx>
recipes/`, the icon beside it, `runtime-python.sh` from the OS tree for
Lutris's runtime, then `./publish.sh`). `apps-test.py` installs each of
them on the live ISO in QEMU and screendumps it.

Layout, publishing and the rules for a recipe are as in
[apm-recipes](https://github.com/Jaxilian/apm-recipes#layout).
