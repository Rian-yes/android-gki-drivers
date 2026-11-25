#!/system/bin/sh
# customize.sh - KernelSU / Magisk installer script for GKI USB Wi-Fi + Bluetooth module.

ui_print "****************************************"
ui_print " GKI USB Wi-Fi + Bluetooth (RTL8723DU)"
ui_print "****************************************"

ui_print "- Setting permissions for module scripts and binaries..."
set_perm_recursive "$MODPATH" 0 0 0755 0644
for f in "$MODPATH/service.sh" "$MODPATH/action.sh" "$MODPATH/uninstall.sh" "$MODPATH/bin/btup"; do
  [ -f "$f" ] && set_perm "$f" 0 0 0755
done

if [ -d "$MODPATH/vendor/firmware" ]; then
  ui_print "- Staging Wi-Fi & BT firmware via KernelSU native /vendor/firmware mount..."
  set_perm_recursive "$MODPATH/vendor/firmware" 0 2000 0755 0644
fi

ui_print "- Installation complete. Reboot to load modules."
