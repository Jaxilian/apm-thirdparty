#!/bin/sh
# Bump both package indexes to the newest upstream versions, build what
# moved, and publish what built. Run weekly by the user timer aos-bump.timer
# (README.md, "The weekly bump"); by hand: ./auto-bump.sh
#
#   1. each repo must be clean and on its upstream: the commit made here may
#      carry nothing but the bumped recipe files
#   2. bump.py --write rewrites the recipes with a newer upstream (it checks
#      each download against the vendor's checksum where there is one)
#   3. every changed recipe is built with apm, which downloads the source
#      again and verifies the new sha256; a recipe that fails is put back
#      with git checkout and listed as skipped, so it is never published
#   4. the built ones are committed, pushed, and the index published
#
# The log is ~/Projects/OS/tmp-build/auto-bump/<date>.log, the summary its
# last paragraph. Nothing moved = exit 0 and nothing on stdout.

set -eu
OS=$HOME/Projects/OS
APM=${APM:-$OS/apm/target/release/apm}
BUMP=$OS/apm-thirdparty/bump.py
REPOS="$OS/apm-thirdparty $OS/apm-recipes"
# On disk, not /tmp: /tmp is a 16 GB tmpfs shared with the image builds,
# and a Blender or Claude Desktop unpack is several hundred MB.
WORK=$OS/tmp-build/auto-bump
mkdir -p "$WORK/tmp"
export TMPDIR="$WORK/tmp"
LOG=$WORK/$(date +%F).log
exec 3>&1 >>"$LOG" 2>&1
echo "== auto-bump $(date -Is)"

# 60 API calls an hour without a token; both indexes together need more.
# gh keeps it in the login keyring: with jax logged out the keyring may be
# locked, and then this run stops here rather than publish half.
GITHUB_TOKEN=$(gh auth token) || { echo "abort: no token from gh auth token"; exit 1; }
export GITHUB_TOKEN

ver() { sed -n 's/^version *= *"\(.*\)"/\1/p' | head -1; }

for repo in $REPOS; do
	if [ -n "$(git -C "$repo" status --porcelain)" ]; then
		echo "abort: $repo is not clean:"; git -C "$repo" status --short
		exit 1
	fi
	# The GitHub workflow's bump PR may have been merged since the last run.
	git -C "$repo" pull --ff-only
done

moved="" skipped="" published="" msg="" title=""
for repo in $REPOS; do
	name=$(basename "$repo")
	echo "-- $name"
	python3 -I "$BUMP" --root "$repo" --write --dl "$WORK/dl" >"$WORK/table" 2>&1
	cat "$WORK/table"
	# bump.py's own skips: an upstream down, a checksum that did not match.
	skipped="$skipped$(sed -n 's/^| \([^ ]*\) | .* | skip: \(.*\) | *|$/\1 (\2); /p' "$WORK/table")"
	built=""
	for f in $(git -C "$repo" diff --name-only -- recipes/); do
		pkg=$(basename "$(dirname "$f")" | sed 's/\./\//')
		old=$(git -C "$repo" show "HEAD:$f" | ver)
		new=$(ver <"$repo/$f")
		dir=$(mktemp -d "$WORK/build.XXXXXX")
		echo "-- build $pkg $new"
		if (cd "$dir" && "$APM" --user build "$repo/$f" </dev/null); then
			built="$built $f"
			moved="$moved$pkg $old -> $new; "
			msg="${msg:-}$pkg $old -> $new
"
			title="${title:+$title, }$pkg $new"
		else
			git -C "$repo" checkout -- "$f"
			skipped="$skipped$pkg $new (the build failed); "
		fi
		rm -rf "$dir"
	done
	if [ -n "$built" ]; then
		# Only the files that built: anything else in the tree aborted above.
		git -C "$repo" add -- $built
		git -C "$repo" commit -q -F - <<EOF
Bumps of $(date +%F): $title

$msg
Built with apm build (the source downloaded and its sha256 verified)
by auto-bump.sh; the apps were not run.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
		git -C "$repo" push
		(cd "$repo" && APM="$APM" ./publish.sh)
		published="$published $name"
	fi
	msg="" title=""
done
rm -rf "$WORK/dl" "$WORK/tmp" "$WORK/table"

if [ -z "$published" ]; then
	echo "auto-bump $(date +%F): nothing moved.${skipped:+ Skipped: $skipped}"
	exit 0
fi
summary="auto-bump $(date +%F): moved: ${moved:-nothing}. Skipped: ${skipped:-nothing}. Published:$published."
echo
echo "$summary"
echo "$summary" >&3
