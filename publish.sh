#!/bin/sh
# Rebuild index/ from recipes/ and the packages in it, sign the stamp with
# the local apm key, and upload the lot as the assets of the `index`
# Release -- a fixed tag, so every URL a client uses is stable:
#
#   https://github.com/Jaxilian/apm-thirdparty/releases/download/index/
#
# Every URL in the index is relative to that directory: a package is an
# asset, a recipe is an asset, a recipe's source tarball is an asset. The
# index zip is named by its own sha256 and the stamp says which one is
# current, so a stale download can never sit under a fresh name.
#
#   ./publish.sh          build and upload
#   ./publish.sh --local  build only; index/ is left for inspection
#
# Needs: apm on PATH (or APM=/path/to/apm), a key from `apm key new`, and
# gh logged in. index/ is not in git: it is what this script produces.

set -e
cd "$(dirname "$0")"
APM=${APM:-apm}
TAG=index

# A recipe's source of truth is recipes/<xx>/<org>.<name>/recipe.toml; the
# index directory gets a flat copy, named so `apm index` can list it.
rm -f index/*.recipe.toml
for r in recipes/*/*/recipe.toml; do
	[ -f "$r" ] || continue
	cp "$r" "index/$(basename "$(dirname "$r")").recipe.toml"
done

# Icons for the store, named <org>.<name>.svg or .png: `apm index` puts
# each in the index's meta.tsv with its hash, and they upload with the rest.
for i in icons/*; do
	[ -f "$i" ] && cp "$i" index/
done

# Only the newest release of each org-name-version stays in index/, unless
# keep.txt (tracked, beside this script) or index/keep.txt (what a
# generator may write) lists the build as <org>-<name>-<version>-<release>,
# one per line, # comments allowed: an app's closure names the exact build
# it runs on, and that build must stay fetchable. The list is kept by hand
# for now; P5 (closures in Software) will generate it from every published
# app's newest closure plus the one before. An apm before 0.1.25 took the
# first release listed, not the highest (aos/perl 5.42.3-2 over -5,
# 2026-10-23), and machines on AOS 0.4.6 and older still run one, so the
# kept builds are listed after the newest by `apm index` (v0.1.25+ sorts).
# The release is the number before ".<arch>.apkg".
# Before the older one goes, the soname gate (the OS tree's
# board/aos/soname-gate.sh): a new release of the same version must keep
# every library the old one had, or installed apps stop starting at the
# next Update. This machine publishes (it holds the key) and has the OS
# tree beside this repository; a missing gate stops the publish.
GATE=${SONAME_GATE:-../aos/br2ext/board/aos/soname-gate.sh}
keep=$(cat keep.txt index/keep.txt 2>/dev/null | sed 's/#.*//; s/ //g' | grep -v '^$' || true)
for f in index/*.apkg; do
	[ -f "$f" ] || continue
	base=${f%-*.*.apkg}
	newest=$(ls "$base"-*.apkg | sed 's/.*-\([0-9]*\)\.[^.]*\.apkg$/\1 &/' | sort -n | tail -1 | cut -d' ' -f2)
	[ "$f" = "$newest" ] && continue
	"$GATE" "$f" "$newest"
	b=$(basename "$f"); b=${b%.*.apkg}
	if echo "$keep" | grep -qx "$b"; then
		echo "kept: $f (keep.txt)"; continue
	fi
	echo "superseded: $f"; rm -f "$f"
done

"$APM" index index --sign
[ "$1" = --local ] && exit 0

# One release, created once; every publish replaces its assets. Old index
# zips are removed so the release does not accumulate one per publish.
gh release view "$TAG" >/dev/null 2>&1 || \
	gh release create "$TAG" --title "package index" \
		--notes "The apm package index. Point apm at this release's download URL." \
		--latest=false
for old in $(gh release view "$TAG" --json assets -q '.assets[].name' | grep '^index-.*\.zip$'); do
	[ -f "index/$old" ] || gh release delete-asset "$TAG" "$old" -y
done
# The upload fails transiently now and then; the second attempt has always
# gone through. --clobber makes a retry safe.
n=0
until gh release upload "$TAG" --clobber index/*; do
	n=$((n + 1)); [ $n -lt 3 ] || exit 1
	echo "upload failed, retrying ($n)" >&2; sleep 5
done
echo "published: https://github.com/Jaxilian/apm-thirdparty/releases/download/$TAG/stamp.toml"
echo "note: the edge cache serves the previous stamp.toml for a few minutes" >&2
