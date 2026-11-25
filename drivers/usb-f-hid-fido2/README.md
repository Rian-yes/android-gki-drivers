# Hardened USB HID Gadget (`usb_f_hid_fix.ko`) & FIDO2 Security Key

This directory contains the hardened Android GKI USB HID Gadget kernel module (`usb_f_hid_fix.ko`) and the Termux Python FIDO2/CTAP2 userspace security key emulator (`fido_touch_key.py` + `run_fido.sh`).

## Contents
- `f_hid_fix/` — Patched `f_hid.c` driver source, `Makefile`, and reference `Module.symvers`
  - Fixes **CVE-2026-31606** (`cdev_alloc` + KMI-exported `cdev_device_add` in `hidg_alloc`/`hidg_free`) so `/dev/hidg0` survives USB selective suspend / UDC rebinds while open
  - Safe `rmmod` alias tracking in `hidg_modinit()`/`hidg_modexit()`
  - Endpoint & write-queue disconnect guards
- `packaging-f-hid/` — Magisk/KernelSU module packaging scripts
- `userspace/fido_touch_key.py` — CTAP2/CTAPHID FIDO2 passkey emulator (ECDSA P-256, non-blocking `select()` I/O, `-ESHUTDOWN` suspend resilience)
- `userspace/run_fido.sh` — Root launcher with CLI options (`run_fido.sh 1..6`, `-h`/`--help`)

## CI Workflow & Full Guide
- Workflow: [`.github/workflows/build-f-hid.yml`](../../.github/workflows/build-f-hid.yml)
- Detailed guide: [`docs/f_hid_hardening_and_fido2_guide.md`](../../docs/f_hid_hardening_and_fido2_guide.md)
