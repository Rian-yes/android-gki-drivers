#!/system/bin/sh
# action.sh - KernelSU Action button: bind Wi-Fi + BT and toggle managed/monitor mode.
# Firmware is served natively via /vendor/firmware/ (no firmware_class.path override).

MODDIR="${0%/*}"
DRV_NAME="__DRV_NAME__"
USB_ID="__USB_ID__"
BTUP="$MODDIR/bin/btup"

[ -f "$BTUP" ] && chmod 755 "$BTUP" 2>/dev/null || true

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

echo "=== GKI USB Wi-Fi + BT ($DRV_NAME) ==="

# 1) Find adapter
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
  echo "[-] RTL8723DU adapter ($USB_ID) not found on USB."
  echo "[!] Plug in the adapter and press Action again."
  exit 0
fi

# 2) Bluetooth
need_bt_init=0
if [ -d /sys/bus/usb/drivers/btusb ]; then
  if [ -z "$(ls /sys/class/bluetooth 2>/dev/null)" ]; then
    need_bt_init=1
  elif [ -x "$BTUP" ]; then
    "$BTUP" status 0 >/dev/null 2>&1 || need_bt_init=1
  fi
fi

if [ "$need_bt_init" = "1" ]; then
  echo "[*] Initializing Bluetooth..."
  for iface in /sys/bus/usb/devices/${dev}:*; do
    [ -d "$iface" ] || continue
    [ -d "$iface/driver" ] || echo "$(basename "$iface")" > /sys/bus/usb/drivers/btusb/bind 2>/dev/null || true
  done
  sleep 2
  if [ -x "$BTUP" ]; then
    "$BTUP" up 0 >/dev/null 2>&1 || true
  elif command -v hciconfig >/dev/null 2>&1; then
    hciconfig hci0 up 2>/dev/null || true
  fi
  sleep 1
fi

if [ -x "$BTUP" ]; then
  bt_stat=$("$BTUP" status 0 2>/dev/null) \
    && echo "[+] Bluetooth: $bt_stat" \
    || echo "[-] Bluetooth: hci0 DOWN"
elif [ -n "$(ls /sys/class/bluetooth 2>/dev/null)" ]; then
  echo "[+] Bluetooth: $(ls /sys/class/bluetooth | tr '\n' ' ')"
else
  echo "[!] No hci device — check: dmesg | grep -iE 'btusb|rtl_bt'"
fi

# 3) Bind Wi-Fi if absent
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

# 4) Toggle monitor <-> managed
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
