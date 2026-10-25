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
#   4. `apm check` on each build: every library an ELF of it needs, every
#      interpreter and every store path a script names must be in the
#      build, in its dependencies or in the OS baseline (the OS tree's
#      board/aos/abi-baseline.txt, BASELINE=). The dependencies' builds
#      come from the user store (~/.apm/packages/<org>/<name>/current)
#      when installed there, else from the newest .apkg of that package in
#      either repo's index/ (what the publish ships); a dependency found
#      in neither (a recipe-only one, aos/fonts) is noted and the check
#      runs without it. A failed check is put back like a failed build.
#   5. with BUMP_QEMU=1 (the default when AOS_OUT names a build),
#      bump-check.py installs the builds that passed on the installed AOS
#      disk in QEMU, with every recipe depending on them, and starts each
#      app in its sandbox (aos-launch-check); a build that fails there is
#      put back too, with the reason
#   6. the rest are committed, pushed, and the index published
#
# The log is ~/Projects/OS/tmp-build/auto-bump/<date>.log, the summary its
# last paragraph. Nothing moved = exit 0 and nothing on stdout.

set -eu
OS=$HOME/Projects/OS
APM=${APM:-$OS/apm/target/release/apm}
BUMP=$OS/apm-thirdparty/bump.py
QEMU_CHECK=$OS/apm-thirdparty/bump-check.py
REPOS="$OS/apm-thirdparty $OS/apm-recipes"
BASELINE=${BASELINE:-$OS/aos/br2ext/board/aos/abi-baseline.txt}
BUMP_QEMU=${BUMP_QEMU:-$([ -d "${AOS_OUT:-/nonexistent}" ] && echo 1 || echo 0)}
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

# The org/name of each [[dependencies]] table of a recipe, one per line.
deps() {
	awk '/^\[\[dependencies\]\]/ {d=1; n=""; o=""; next}
	     /^\[/ {d=0}
	     d && /^name *=/ {gsub(/.*= *"|".*/, ""); n=$0}
	     d && /^organization *=/ {gsub(/.*= *"|".*/, ""); o=$0}
	     d && n != "" && o != "" {print o "/" n; n=""; o=""}' "$1"
}

# The build of a dependency to check against: --closure org/name=PATH for
# the user store's current build, else the newest .apkg in an index/;
# nothing, and a note, when there is neither.
closure_of() {
	cur=$HOME/.apm/packages/$1/current
	if [ -d "$cur" ]; then echo "--closure $1=$cur"; return; fi
	pkg=$(echo "$1" | tr / -)
	newest=$(ls "$OS"/apm-thirdparty/index/"$pkg"-[0-9]*.apkg "$OS"/apm-recipes/index/"$pkg"-[0-9]*.apkg 2>/dev/null | sort -V | tail -1)
	if [ -n "$newest" ]; then echo "--closure $1=$newest"; return; fi
	echo "note: no build of $1 at hand; apm check runs without it" >&2
}

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
	rm -rf "$WORK/apkgs"
	for f in $(git -C "$repo" diff --name-only -- recipes/); do
		pkg=$(basename "$(dirname "$f")" | sed 's/\./\//')
		new=$(ver <"$repo/$f")
		dir=$(mktemp -d "$WORK/build.XXXXXX")
		echo "-- build $pkg $new"
		# apm build prints the .apkg's path last on stdout.
		if apkg=$(cd "$dir" && "$APM" --user build "$repo/$f" </dev/null | tail -1) && [ -f "$apkg" ]; then
			closures=$(for d in $(deps "$repo/$f"); do closure_of "$d"; done)
			echo "-- check $pkg $new${closures:+ ($closures)}"
			# shellcheck disable=SC2086
			if "$APM" check "$apkg" --baseline "$BASELINE" $closures; then
				mkdir -p "$WORK/apkgs"
				mv "$apkg" "$WORK/apkgs/"
				built="$built $f"
			else
				git -C "$repo" checkout -- "$f"
				skipped="$skipped$pkg $new (apm check: a library, interpreter or package path it needs is not in the build, its dependencies or the OS); "
			fi
		else
			git -C "$repo" checkout -- "$f"
			skipped="$skipped$pkg $new (the build failed); "
		fi
		rm -rf "$dir"
	done
	# The QEMU gate: the builds that passed so far, installed and started
	# on the real disk; what fails there is put back with the reason.
	if [ -n "$built" ] && [ "$BUMP_QEMU" = 1 ]; then
		echo "-- bump-check (QEMU)"
		results=$WORK/bump-check.txt
		BUMP_RESULTS=$results python3 -I "$QEMU_CHECK" "$WORK"/apkgs/*.apkg || true
		for f in $built; do
			pkg=$(basename "$(dirname "$f")" | sed 's/\./\//')
			verdict=$(grep -m1 " $pkg\( \|$\)" "$results" 2>/dev/null || echo "fail $pkg no verdict from bump-check.py")
			case "$verdict" in
			ok*) ;;
			*)
				new=$(ver <"$repo/$f")
				git -C "$repo" checkout -- "$f"
				skipped="$skipped$pkg $new (QEMU: ${verdict#fail $pkg }); "
				built=$(echo "$built" | sed "s| $f||")
				;;
			esac
		done
		rm -rf "$WORK/apkgs" "$results"
	elif [ -n "$built" ]; then
		echo "-- bump-check skipped (BUMP_QEMU=$BUMP_QEMU; set AOS_OUT to a build to run it)"
		rm -rf "$WORK/apkgs"
	fi
	for f in $built; do
		pkg=$(basename "$(dirname "$f")" | sed 's/\./\//')
		old=$(git -C "$repo" show "HEAD:$f" | ver)
		new=$(ver <"$repo/$f")
		moved="$moved$pkg $old -> $new; "
		msg="${msg:-}$pkg $old -> $new
"
		title="${title:+$title, }$pkg $new"
	done
	if [ -n "$built" ]; then
		# Only the files that built: anything else in the tree aborted above.
		git -C "$repo" add -- $built
		git -C "$repo" commit -q -F - <<EOF
Bumps of $(date +%F): $title

$msg
Built with apm build (the source downloaded and its sha256 verified),
checked with apm check against the OS baseline and the dependencies'
builds$([ "$BUMP_QEMU" = 1 ] && echo ", installed and started on the AOS disk in QEMU (bump-check.py)" || echo "; the apps were not run"), by auto-bump.sh.

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
moved=${moved%; } skipped=${skipped%; }
summary="auto-bump $(date +%F): moved: ${moved:-nothing}. Skipped: ${skipped:-nothing}. Published:$published."
echo
echo "$summary"
echo "$summary" >&3
