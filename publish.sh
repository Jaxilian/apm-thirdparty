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
