#!/bin/sh
set -eu
yes=0
[ "${1:-}" = --yes ] && { yes=1; shift; }
dir=${1:-$(dirname "$0")/../index}
for f in "$dir"/*.apkg; do
	[ -f "$f" ] || continue
	lic=$(python3 -I -c 'import sys, zipfile; sys.stdout.write(zipfile.ZipFile(sys.argv[1]).read("manifest.toml").decode())' "$f" | sed -n 's/^license *= *"\(.*\)"/\1/p')
	[ -n "$lic" ] || { echo "recipe-only-prune: no license in $f" >&2; exit 1; }
	case "$lic" in
	*LicenseRef-*) ;;
	*) continue ;;
	esac
	echo "$f"
	if [ "$yes" = 1 ]; then
		rm -f "$f"
		echo "deleted: $f ($lic)" >&2
	else
		echo "would delete: $f ($lic); --yes deletes" >&2
	fi
done
