#!/system/bin/sh
# uninstall.sh - Clean up module runtime files on removal.
# Kernel modules are not rmmod'd on the live kernel; a reboot cleanly reverts them.

MODDIR="${0%/*}"
rm -f "$MODDIR/load.log" 2>/dev/null || true
