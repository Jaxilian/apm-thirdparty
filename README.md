# apm-thirdparty

**Packages here are not part of AOS.** They are compatibility software —
toolkits and applications written for other systems, carried over so
that the things people expect to run, run. They may not work, and an
update to them or to the OS may break them. That is the deal, and apm says
so when you add this repository.

The OS's own packages live in [apm-recipes](https://github.com/Jaxilian/apm-recipes).
A third-party package may depend on official ones; never the reverse.

## Use it

In Software: the Third-party page's switch. From a terminal (since AOS
0.4.31 there is no sudo: apm asks aos-rootd, which asks your password
once per 15 minutes):

```sh
apm repo add thirdparty
apm update
apm find code          # marked [third-party]
apm install vscode
```

The key is the same as the official repository's; trusting it once covers
both.

## What goes here

The GTK3 runtime and what runs on it; Electron applications (VS Code,
Discord, Spotify); Firefox and Chrome; Steam and Lutris; the creative
tools people ask for (Blender, Krita, Inkscape); Thunderbird; the
developer toolchains (Node.js, Java, .NET, Python), unsandboxed like
Rust in the official repository; and the Python runtime Lutris runs on. Vendor binaries are never mirrored: a recipe points at
the vendor's own download and pins its checksum.

Chrome, Spotify, Blender, Krita, Inkscape and Lutris fetch `.deb` files
or AppImages, which apm reads from v0.1.13 on, so they need an AOS
release with that apm (0.3.0) to build; on 0.2.9 they are listed and
fail. Lutris runs on `runtime/python`, built by `runtime-python.sh` in
the OS tree. `apps-test.py` installs each of them on the live ISO in QEMU
and screendumps it.

Layout, publishing and the rules for a recipe are as in
[apm-recipes](https://github.com/Jaxilian/apm-recipes#layout).

## Keeping the recipes current

`bump.py` (Python 3, standard library only) looks up each recipe's newest
upstream version and, when there is one, rewrites the recipe: version,
the source URL and sha256 (the file is downloaded and checked against the
checksum the vendor publishes, where there is one), `release = 1`. How to
look is in an `update.toml` beside the recipe, one `[[source]]` per
source: a GitHub release, an apt Packages index, a listing page and a
regex, a redirect, a JSON API, Node's or .NET's release metadata; the
format is in bump.py's docstring. A recipe without one is left alone.
apm reads recipe.toml strictly (unknown tables are an error), which is
why the rule is a sidecar; publish.sh does not copy it.

```sh
python3 -I bump.py                   # dry run: recipe, current, newest, action
python3 -I bump.py --write           # rewrite what has a newer upstream
python3 -I bump.py --only zed/zed    # one recipe
```

`.github/workflows/bump.yml` runs it daily on GitHub's hosted runner and
opens, or refreshes, the pull request `bump/auto` with the table. It
cannot publish: the index is signed with the key on the owner's machine.
The routine is three steps:

1. Read the PR (the table says what each checksum was checked against;
   the apps were not run) and merge it. Optionally first
   `python3 apps-test.py <name>...` for the bumped apps.
2. `git pull`
3. `./publish.sh`

The workflow needs "Allow GitHub Actions to create and approve pull
requests" switched on under Settings, Actions, General.

## The weekly bump

`auto-bump.sh` does the routine above on this machine, for both indexes
(this one and apm-recipes, through `bump.py --root`), and publishes
without a PR: it runs `bump.py --write`, builds every changed recipe with
`apm --user build` (the new source is downloaded and its sha256
verified), puts back with `git checkout` any recipe that failed to build,
commits and pushes only the recipes that built, and runs each repo's
`publish.sh`. A repo with uncommitted changes, or one that cannot
fast-forward to origin, stops the run before anything is written.

- **When:** the systemd user timer `aos-bump.timer`, Mondays 06:00;
  `Persistent=true` runs a missed Monday at the next boot. Linger is on
  for jax, so it runs logged out too -- but `gh auth token` reads the
  login keyring, and with it locked the run stops at the start.
- **Log:** `~/Projects/OS/tmp-build/auto-bump/<date>.log`; its last
  paragraph says what moved, what was skipped and why, what was published.
  Nothing moved = exit 0, nothing on stdout.
- **By hand:** `./auto-bump.sh` (or `systemctl --user start aos-bump.service`).
  The dry run is still `python3 -I bump.py [--root ../apm-recipes]`.
- **Pause:** `systemctl --user disable --now aos-bump.timer`; `enable --now`
  to resume. `systemctl --user list-timers aos-bump.timer` shows the next run.

The apps are built, not run: `apps-test.py` stays the owner's check.

## The three gates

A bump that adds a library or a tool nobody noticed used to be found by
the person whose app stopped starting. Three checks stand between a
build and the index now, cheapest first:

1. **The soname gate** (`publish.sh`, and `runtime-*.sh` in the OS
   tree): a new release of the same version must keep every library the
   previous release had, or go up a version. Software's Update moves
   every app to a new release, and a library dropped in one is an app
   that dies at start. The OS has the same rule for its core
   (`abi-baseline.txt`, release.sh).
2. **`apm check`** (`auto-bump.sh` after each build; by hand on any
   `.apkg` or unpacked build): every library an ELF of the build needs
   must be found in the loader's order, in its own RUNPATH, in the
   build, in its dependencies' builds (`--closure org/name=DIR|.apkg`)
   or in the OS baseline (`--baseline FILE`; the OS tree's
   `board/aos/abi-baseline.txt` by default). A 32-bit program resolves
   only against 32-bit libraries, never the baseline. Every `#!` of a
   script under `bin/` must name an interpreter that exists, and every
   literal `/opt/apm/packages/<org>/<name>` must name the package or a
   dependency (Lutris's python, gtk3 and java paths). What a program
   loads by name (GTK modules, GL, Electron's) is not in NEEDED: a
   recipe lists those under `[check] dlopen = ["libgtk-3.so.0"]`. It
   cannot see a payload downloaded at first start (Steam's runtime).
3. **`bump-check.py`** (`auto-bump.sh` with `BUMP_QEMU=1`, on by default
   when `AOS_OUT` names a build): the builds that passed are installed
   on the installed AOS disk in QEMU, with every recipe of either index
   that depends on them, and each app is started in its real sandbox by
   `aos-launch-check`. A FAIL puts the recipe back, and the log says why.

A build that fails any gate is not published; `auto-bump.sh`'s summary
names it with the reason. `publish.sh` prunes superseded releases after
the gate, except those `keep.txt` lists (`<org>-<name>-<version>-<release>`,
one per line): the exact builds installed apps' closures still name.
The list is kept by hand until Software generates it.
