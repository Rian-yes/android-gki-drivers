#!/data/data/com.termux/files/usr/bin/bash
# run_fido.sh - Interactive & CLI launcher for FIDO2 Security Key Emulator

# Immediate help without needing root
if [ "$1" = "-h" ] || [ "$1" = "--help" ] || [ "$1" = "help" ]; then
    echo "======================================================================"
    echo "  🛡️  FIDO2 / WebAuthn Hardware Security Key Emulator (Galaxy A35)"
    echo "======================================================================"
    echo ""
    echo "Usage: ./run_fido.sh [OPTION]"
    echo ""
    echo "Options:"
    echo "  1, -1, --interactive   Start key in INTERACTIVE TOUCH mode (Press [ENTER] to touch)"
    echo "  2, -2, --auto          Start key in AUTO-TOUCH mode (Instant touch emulation)"
    echo "  3, -3, --list          View saved passkeys / credentials"
    echo "  4, -4, --reset-pin     Reset security key PIN"
    echo "  5, -5, --bind          Re-bind USB ConfigFS gadget (/dev/hidg0)"
    echo "  6, -6, --unbind        Unbind USB gadget (Restore normal phone USB)"
    echo "  -h, --help             Display this help message and exit"
    echo ""
    echo "Examples:"
    echo "  ./run_fido.sh 1              # Run directly in interactive touch mode"
    echo "  ./run_fido.sh 2              # Run directly in auto-touch mode"
    echo "  ./run_fido.sh 3              # List all stored passkeys"
    echo "  ./run_fido.sh                # Open interactive menu"
    echo "======================================================================"
    exit 0
fi

export PATH=/data/data/com.termux/files/usr/bin:$PATH
export LD_LIBRARY_PATH=/data/data/com.termux/files/usr/lib

# Ensure kernel module is loaded from persistent vendor storage
su 0 -c '
    if ! grep -q "usb_f_hid" /proc/modules; then
        if [ -f /data/vendor/fw/usb_f_hid_fix.ko ]; then
            insmod /data/vendor/fw/usb_f_hid_fix.ko 2>/dev/null
        elif [ -f /data/local/tmp/usb_f_hid_fix.ko ]; then
            insmod /data/local/tmp/usb_f_hid_fix.ko 2>/dev/null
        fi
    fi
'

# Forward all CLI arguments into the Python emulator via su 0
exec su 0 -c "PATH=/data/data/com.termux/files/usr/bin:\$PATH LD_LIBRARY_PATH=/data/data/com.termux/files/usr/lib /data/data/com.termux/files/usr/bin/python3 /data/data/com.termux/files/home/fido_touch_key.py $*"
