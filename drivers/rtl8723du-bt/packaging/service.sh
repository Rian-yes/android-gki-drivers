#!/system/bin/sh
# service.sh - KernelSU late_start one-shot service for GKI USB Wi-Fi + BT module.
# Firmware is served natively via KernelSU overlayfs at /vendor/firmware/
# (system/vendor -> ../vendor). Never overrides /sys/module/firmware_class/parameters/path.

MODDIR="${0%/*}"
KMOD="$MODDIR/modules"
DRV_NAME="__DRV_NAME__"
DRV_KO="__DRV_KO__"
USB_ID="__USB_ID__"
BTUP="$MODDIR/bin/btup"

LOG="$MODDIR/load.log"
log() { echo "$(date '+%H:%M:%S') $*" >> "$LOG"; }
: > "$LOG"

[ -f "$BTUP" ] && chmod 755 "$BTUP" 2>/dev/null || true

# 1) Wait for Android boot completion
n=0
while [ "$(getprop sys.boot_completed)" != "1" ] && [ $n -lt 150 ]; do
  sleep 2
  n=$((n + 1))
done
sleep 5

# 2) Verify native /vendor/firmware mount
if [ -f /vendor/firmware/rtw88/rtw8723d_fw.bin ] && \
   [ -f /vendor/firmware/rtl_bt/rtl8723d_fw.bin ]; then
  log "firmware active via native /vendor/firmware mount"
else
  log "WARNING: /vendor/firmware/{rtw88,rtl_bt} not visible (check KernelSU mount)"
fi

# 3) Prevent memory fragmentation for mac80211 queue allocation
sysctl -w vm.compaction_proactiveness=50 2>/dev/null || true
sysctl -w vm.min_free_kbytes=32768 2>/dev/null || true
echo 1 > /proc/sys/vm/compact_memory 2>/dev/null || true

# 4) Load kernel modules in dependency order
echo 0 > /sys/bus/usb/drivers_autoprobe 2>/dev/null || true
for m in "$KMOD"/mac80211.ko \
         "$KMOD"/rtw_core.ko \
         "$KMOD"/rtw_8723x.ko \
         "$KMOD"/rtw_8723d.ko \
         "$KMOD"/rtw_usb.ko \
         "$KMOD"/"$DRV_KO" \
         "$KMOD"/btintel.ko "$KMOD"/btrtl.ko "$KMOD"/btusb.ko \
         "$KMOD"/bnep.ko "$KMOD"/hci_vhci.ko \
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

find_wifi_iface() {
  for net in /sys/bus/usb/drivers/"$DRV_NAME"/*:*/net/*; do
    [ -d "$net" ] || continue
    basename "$net"
    return 0
  done
  ip link show wlan1 >/dev/null 2>&1 && echo "wlan1" && return 0
  return 1
}

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

# 5) Bind at boot if adapter is already attached
dev=$(find_adapter 2>/dev/null)
if [ -n "$dev" ]; then
  log "found adapter $dev at boot, binding..."
  echo 1 > /proc/sys/vm/compact_memory 2>/dev/null || true
  for iface in /sys/bus/usb/devices/${dev}:*; do
    [ -d "$iface" ] || continue
    [ -d "$iface/driver" ] && continue
    ib=$(basename "$iface")
    echo "$ib" > "/sys/bus/usb/drivers/$DRV_NAME/bind" 2>/dev/null || true
    [ -d "$iface/driver" ] && continue
    [ -d /sys/bus/usb/drivers/btusb ] && echo "$ib" > /sys/bus/usb/drivers/btusb/bind 2>/dev/null || true
  done
  sleep 2
  if [ -x "$BTUP" ]; then
    "$BTUP" up 0 >/dev/null 2>&1 || true
  elif command -v hciconfig >/dev/null 2>&1; then
    hciconfig hci0 up 2>/dev/null || true
  fi
  sleep 1
  wiface=$(find_wifi_iface 2>/dev/null)
  log "wifi: ${wiface:-absent}, bt: $("$BTUP" status 0 2>/dev/null || echo 'checked')"
else
  log "adapter not attached at boot — kernel drivers_autoprobe will handle hotplug"
fi

log "service.sh complete"
