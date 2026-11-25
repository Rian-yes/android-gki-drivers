#!/system/bin/sh
# action.sh - KernelSU Action button: bind USB Wi-Fi & toggle managed/monitor mode.
# Firmware is served natively via /vendor/firmware/ (no firmware_class.path override).

MODDIR="${0%/*}"
DRV_NAME="__DRV_NAME__"
USB_ID="__USB_ID__"

IW=""
IW_MODE="direct"
for candidate in /system/bin/iw /vendor/bin/iw /data/data/com.termux/files/usr/bin/iw; do
  if [ -x "$candidate" ]; then
    IW="$candidate"
    [ "$candidate" = "/data/data/com.termux/files/usr/bin/iw" ] && IW_MODE="termux"
    break
  fi
done

if [ -z "$IW" ] && [ -x /data/local/nhsystem/kali-arm64/usr/sbin/iw ] && command -v chroot >/dev/null 2>&1; then
  IW="/usr/sbin/iw"
  IW_MODE="kali_chroot"
fi

iw_cmd() {
  case "$IW_MODE" in
    termux)
      LD_LIBRARY_PATH=/data/data/com.termux/files/usr/lib "$IW" "$@"
      ;;
    kali_chroot)
      chroot /data/local/nhsystem/kali-arm64 "$IW" "$@"
      ;;
    *)
      "$IW" "$@"
      ;;
  esac
}

get_mode_sysfs() {
  type=$(cat "/sys/class/net/$1/type" 2>/dev/null)
  case "$type" in
    803) echo "monitor" ;;
    1)   echo "managed" ;;
    *)   echo "unknown($type)" ;;
  esac
}

find_wifi_iface() {
  for net in /sys/bus/usb/drivers/"$DRV_NAME"/*:*/net/*; do
    [ -d "$net" ] || continue
    basename "$net"
    return 0
  done
  ip link show wlan1 >/dev/null 2>&1 && echo "wlan1" && return 0
  return 1
}

echo "=== GKI USB Wi-Fi ($DRV_NAME) ==="

# Find adapter on USB bus
dev=""
for d in /sys/bus/usb/devices/*/idProduct; do
  dir=$(dirname "$d")
  [ -f "$dir/idVendor" ] || continue
  id="$(cat "$dir/idVendor" 2>/dev/null):$(cat "$d" 2>/dev/null)"
  if [ "$id" = "$USB_ID" ] || [ "$id" = "0bda:d723" ] || [ "$id" = "7392:d611" ]; then
    dev=$(basename "$dir")
    break
  fi
done

if [ -z "$dev" ]; then
  echo "[-] Adapter ($USB_ID) not detected on USB."
  echo "[!] Plug in the adapter/hub and press Action again."
  exit 0
fi

# Bind Wi-Fi if netdev absent
WIFACE=$(find_wifi_iface 2>/dev/null)
if [ -z "$WIFACE" ]; then
  echo "[*] Binding adapter ($dev)..."
  echo 1 > /proc/sys/vm/compact_memory 2>/dev/null || true
  for iface in /sys/bus/usb/devices/${dev}:*; do
    [ -d "$iface" ] || continue
    [ -d "$iface/driver" ] && continue
    echo "$(basename "$iface")" > "/sys/bus/usb/drivers/$DRV_NAME/bind" 2>/dev/null || true
  done
  sleep 2
  WIFACE=$(find_wifi_iface 2>/dev/null)
fi

if [ -z "$WIFACE" ]; then
  echo "[-] Wi-Fi interface did not come up — check: dmesg | grep -iE 'rtw|firmware'"
  exit 1
fi

# Toggle managed <-> monitor mode
if [ -n "$IW" ]; then
  cur=$(iw_cmd dev "$WIFACE" info 2>/dev/null | grep "type " | awk '{print $2}')
else
  cur=$(get_mode_sysfs "$WIFACE")
fi
[ -z "$cur" ] && cur=$(get_mode_sysfs "$WIFACE")
echo "[+] $WIFACE is UP — mode: ${cur:-unknown}"

if [ -z "$IW" ]; then
  ip link set "$WIFACE" up 2>/dev/null
  echo "[!] 'iw' not found; cannot toggle monitor mode."
  exit 0
fi

if [ "$cur" = "monitor" ]; then
  target="managed"
else
  target="monitor"
fi

ip link set "$WIFACE" down 2>/dev/null
iw_err=$(iw_cmd dev "$WIFACE" set type "$target" 2>&1)
iw_rc=$?
ip link set "$WIFACE" up 2>/dev/null

after=$(iw_cmd dev "$WIFACE" info 2>/dev/null | grep "type " | awk '{print $2}')
[ -z "$after" ] && after=$(get_mode_sysfs "$WIFACE")

if [ "$iw_rc" -eq 0 ] && [ "$after" = "$target" ]; then
  echo "[->] Switched $WIFACE to: $(echo "$target" | tr 'a-z' 'A-Z')"
else
  echo "[-] Failed to switch $WIFACE to $target (now: $after)"
  [ -n "$iw_err" ] && echo "    $iw_err"
  exit 1
fi
