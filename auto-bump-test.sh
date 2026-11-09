set -eu
WORK=$(mktemp -d)
eval "$(sed -n '/^HELD=/,/^ver()/p' "$(dirname "$0")/auto-bump.sh" | sed '$d')"
ok() { echo "ok $1"; }
remember aos/blender 4.6.1 "the build failed"
[ "$(held aos/blender 4.6.1)" = "the build failed, $(date +%F)" ] && ok a_failed_bump_is_held_at_its_version
[ -z "$(held aos/blender 4.6.2)" ] && ok a_newer_upstream_version_is_tried_again
remember aos/blender 4.6.2 "QEMU: no window	in 30 s"
[ "$(wc -l < "$HELD")" = 1 ] && ok a_package_has_one_remembered_failure
held aos/blender 4.6.2 | grep -q 'no window in 30 s' && ok a_reason_with_a_tab_stays_one_field
awk -F '\t' -v OFS='\t' '{ $3 = $3 - 15 * 86400 } 1' "$HELD" > "$HELD.t" && mv "$HELD.t" "$HELD"
[ -z "$(held aos/blender 4.6.2)" ] && ok after_fourteen_days_the_bump_is_tried_again
remember aos/x 1 "apm check failed"; forget aos/x
[ -z "$(held aos/x 1)" ] && ok a_bump_that_moved_is_forgotten
rm -rf "$WORK"
