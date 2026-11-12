#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
WORK=$(mktemp -d)
ok() { echo "ok $1"; }
no() { echo "FAIL $1"; exit 1; }
pack() {
	python3 -I -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1], "w").writestr("manifest.toml", "[package]\nlicense = \"%s\"\n" % sys.argv[2])' "$WORK/$1" "$2"
}
pack free-app-1.0.0-1.x86_64.apkg "GPL-3.0-only"
pack shut-app-1.0.0-1.x86_64.apkg "LicenseRef-Shut-Terms AND MIT"
[ "$(tools/recipe-only-prune.sh "$WORK" 2>/dev/null)" = "$WORK/shut-app-1.0.0-1.x86_64.apkg" ] && ok a_licenseref_package_is_listed_and_a_free_one_is_not || no a_licenseref_package_is_listed_and_a_free_one_is_not
[ -f "$WORK/shut-app-1.0.0-1.x86_64.apkg" ] && ok nothing_is_deleted_without_yes || no nothing_is_deleted_without_yes
tools/recipe-only-prune.sh --yes "$WORK" >/dev/null 2>&1
[ "$(ls "$WORK")" = free-app-1.0.0-1.x86_64.apkg ] && ok yes_deletes_only_the_licenseref_package || no yes_deletes_only_the_licenseref_package
: > "$WORK/empty-app-1.0.0-1.x86_64.apkg"
tools/recipe-only-prune.sh "$WORK" >/dev/null 2>&1 && no a_package_without_a_license_stops_the_run || ok a_package_without_a_license_stops_the_run
eval "$(sed -n '/^recipe_only_assets() {/,/^}/p' publish.sh)"
got=$(printf '%s\n' zoom-zoom-7.2.1-3.x86_64.apkg zoom.zoom.recipe.toml anthropic-claude-code-2.1.311-1.x86_64.apkg google-android-studio-2026.2.2-2.x86_64.apkg mozilla-firefox-159.0.0-2.x86_64.apkg runtime-gtk3-3.24.0-10.x86_64.apkg | recipe_only_assets | tr '\n' ' ')
[ "$got" = "zoom-zoom-7.2.1-3.x86_64.apkg anthropic-claude-code-2.1.311-1.x86_64.apkg google-android-studio-2026.2.2-2.x86_64.apkg " ] && ok only_the_built_packages_of_licenseref_recipes_leave_the_release || no only_the_built_packages_of_licenseref_recipes_leave_the_release
rm -rf "$WORK"
