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
Discord, Spotify); Firefox; Steam, when a 32-bit userspace exists. Vendor
binaries are never mirrored: a recipe points at the vendor's own download
and pins its checksum.

Layout, publishing and the rules for a recipe are as in
[apm-recipes](https://github.com/Jaxilian/apm-recipes#layout).
