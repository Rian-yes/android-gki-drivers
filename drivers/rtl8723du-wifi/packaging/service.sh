#!/system/bin/sh
# service.sh - KernelSU late_start one-shot service for GKI USB Wi-Fi module.
# Firmware is served natively via KernelSU overlayfs at /vendor/firmware/
# (system/vendor -> ../vendor). Never overrides /sys/module/firmware_class/parameters/path.

MODDIR="${0%/*}"
KMOD="$MODDIR/modules"
USB_ID="__USB_ID__"
DRV_KO="__DRV_KO__"
DRV_NAME="__DRV_NAME__"

LOG="$MODDIR/load.log"
log() { echo "$(date '+%H:%M:%S') $*" >> "$LOG"; }
: > "$LOG"

# 1) Wait for Android boot completion
n=0
while [ "$(getprop sys.boot_completed)" != "1" ] && [ $n -lt 150 ]; do
  sleep 2
  n=$((n + 1))
done
sleep 5

# 2) Verify native /vendor/firmware mount
if [ -d /vendor/firmware/rtw88 ]; then
  log "firmware active via native /vendor/firmware mount"
else
  log "WARNING: /vendor/firmware/rtw88 not visible (check KernelSU mount)"
fi

# 3) Prevent memory fragmentation for mac80211 queue allocation
sysctl -w vm.compaction_proactiveness=50 2>/dev/null || true
sysctl -w vm.min_free_kbytes=32768 2>/dev/null || true
echo 1 > /proc/sys/vm/compact_memory 2>/dev/null || true

# 4) Load kernel modules once in dependency order
echo 0 > /sys/bus/usb/drivers_autoprobe 2>/dev/null || true
for m in "$KMOD"/mac80211.ko \
         "$KMOD"/rtw_core.ko \
         "$KMOD"/rtw_88xxa.ko \
         "$KMOD"/rtw_8723x.ko \
         "$KMOD"/rtw_8723d.ko \
         "$KMOD"/rtw_8821c.ko \
         "$KMOD"/rtw_8822b.ko \
         "$KMOD"/rtw_8822c.ko \
         "$KMOD"/rtw_8812a.ko \
         "$KMOD"/rtw_8814a.ko \
         "$KMOD"/rtw_8821a.ko \
         "$KMOD"/rtw_usb.ko \
         "$KMOD"/"$DRV_KO" \
         "$KMOD"/*.ko; do
  [ -e "$m" ] || continue
  base=$(basename "$m" .ko)
  lsn=$(echo "$base" | tr '-' '_')
  lsmod | grep -q "^$lsn " && continue
  err=$(insmod "$m" 2>&1)
  if [ $? -eq 0 ]; then
    log "loaded $base"
  else
    log "FAILED $base: $err"
  fi
done
echo 1 > /sys/bus/usb/drivers_autoprobe 2>/dev/null || true

# 5) Bind at boot if adapter is already attached
find_adapter() {
  for d in /sys/bus/usb/devices/*/idProduct; do
    dir=$(dirname "$d")
    [ -f "$dir/idVendor" ] || continue
    id="$(cat "$dir/idVendor" 2>/dev/null):$(cat "$d" 2>/dev/null)"
    if [ "$id" = "$USB_ID" ] || [ "$id" = "0bda:d723" ] || [ "$id" = "7392:d611" ]; then
      basename "$dir"
      return 0
    fi
  done
  return 1
}

dev=$(find_adapter 2>/dev/null)
if [ -n "$dev" ]; then
  log "found adapter $dev at boot, binding..."
  echo 1 > /proc/sys/vm/compact_memory 2>/dev/null || true
  for iface in /sys/bus/usb/devices/${dev}:*; do
    [ -d "$iface" ] || continue
    [ -d "$iface/driver" ] && continue
    echo "$(basename "$iface")" > "/sys/bus/usb/drivers/$DRV_NAME/bind" 2>/dev/null || true
  done
  sleep 2
  log "adapter $dev bind attempted"
else
  log "adapter not attached at boot — kernel drivers_autoprobe will handle hotplug"
fi

log "service.sh complete"
