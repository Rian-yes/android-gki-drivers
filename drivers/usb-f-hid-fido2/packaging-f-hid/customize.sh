#!/system/bin/sh
# customize.sh - KernelSU / Magisk installer script for GKI USB HID Gadget Fix.

ui_print "****************************************"
ui_print " GKI USB HID Gadget Fix (FIDO2)"
ui_print "****************************************"

ui_print "- Setting permissions..."
set_perm_recursive "$MODPATH" 0 0 0755 0644
for f in "$MODPATH/service.sh" "$MODPATH/action.sh" "$MODPATH/uninstall.sh"; do
  [ -f "$f" ] && set_perm "$f" 0 0 0755
done

ui_print "- Installation complete. Reboot to load usb_f_hid_fix.ko at boot."
