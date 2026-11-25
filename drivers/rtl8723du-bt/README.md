# RTL8723DU Wi-Fi + BLE / Bluetooth Driver (`bt-test`)

This directory contains the Bluetooth / BLE bringup additions for the Realtek RTL8723DU USB combo adapter on Android GKI (`5.15.153-android13`), extracted from the `bt-test` branch.

## Contents
- `btup.c` — Userspace Bluetooth bringup helper
- `device/a356e-bt-core.config` — Kernel config fragment for Bluetooth core (`BT`, `BT_HCIBTUSB`, `BT_RTL`, etc.)
- `needed-symbols.txt` — Required kernel symbols for Bluetooth module verification
- `scripts/check-bt-symbols.sh` — Verifies required BT symbols against the target kernel
- `packaging/` — Magisk/KernelSU module templates including `bin/btup`

## CI Workflow
Built via [`.github/workflows/build-rtl8723du-bt.yml`](../../.github/workflows/build-rtl8723du-bt.yml).
