#!/bin/sh
# Install the weekly bump timer as a user unit (README.md, "The weekly
# bump"). One short command, since a long line pasted into a terminal
# wraps and breaks (2026-11-02):
#
#   sh ~/Projects/OS/apm-thirdparty/systemd/install.sh
set -e
D="$HOME/.config/systemd/user"
mkdir -p "$D"
cp "$(dirname "$0")/aos-bump.service" "$(dirname "$0")/aos-bump.timer" "$D/"
systemctl --user daemon-reload
systemctl --user enable --now aos-bump.timer
systemctl --user list-timers aos-bump.timer --no-pager
