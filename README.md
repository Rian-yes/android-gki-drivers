# Android GKI Out-of-Tree Kernel Drivers

Hardware-verified out-of-tree Linux kernel modules (`.ko`), GitHub Actions CI build pipelines, Magisk packaging, and userspace tooling for **rooted Android GKI devices** (developed and verified on **Samsung Galaxy A35 5G `SM-A356E`**, Exynos 1380 `s5e8835`, `5.15.153-android13-3-28444519`).

> Only working, hardware-verified driver stacks are included in `main`. Experimental/failed attempts (such as `mx140`) are intentionally excluded.

---

## Reference Device Profile & How Modules Were Compiled

All modules in this repository were built and verified on the following device. The full probe output is saved in [`device-profile.txt`](device-profile.txt).

| Parameter | Observed Value |
| :--- | :--- |
| **Device Model** | **Samsung Galaxy A35 5G (`SM-A356E`, codename `a35x`)** |
| **SoC / Platform** | Samsung Exynos 1380 (`s5e8835` / `erd8835`, `aarch64`) |
| **OS / Build ID** | Android 14 (`UP1A.231005.007.A356EXXS4AYB2`), SELinux `Enforcing` |
| **Kernel Release** | `5.15.153-android13-3-28444519` (`#1 SMP PREEMPT Mon Feb 3 18:37:26 KST 2025`) |
| **Kernel Config Flags** | `CONFIG_MODULES=y`, `CONFIG_MODVERSIONS=y`, `CONFIG_DEBUG_INFO_BTF=y`, `LTO_THIN=y` (`LTO_FULL=n`), `MODULE_SIG_FORCE=n` |

### Exact Compilation Parameters Used for `SM-A356E`
Because Samsung ships `5.15.153-android13-3-28444519` without a git commit hash in `uname -r` and with `CONFIG_MODVERSIONS=y` + `LTO_THIN=y` + `CONFIG_DEBUG_INFO_BTF=y`:
1. **ACK Branch & Commit:** `android13-5.15` at commit **`e787d6ac4af04c06a0120ad773820d43f9b851a9`** (`e787d6ac4af0`) from `android.googlesource.com/kernel/common`.
2. **Compiler & Flags:** Android Clang **`clang-r450784d`** (`ARCH=arm64`, `LLVM=1`, ThinLTO enabled, BTF enabled with `pahole`).
3. **KMI / `module_layout` CRC Matching:** Staged the reference [`Module.symvers`](drivers/usb-f-hid-fido2/f_hid_fix/Module.symvers) into `out/` before `make M=... modules` so `module_layout` and exported symbol CRCs match the running Exynos 1380 GKI kernel.
4. **Live Symbol Verification:** Verified required symbols against `/proc/kallsyms` (`__ksymtab_*`) on the phone before `insmod` (e.g. using `cdev_device_add` instead of unexported `cdev_set_parent`, and `drivers/rtl8723du-bt/scripts/check-bt-symbols.sh` for Bluetooth).
5. **On-Device Deployment:** Loaded via Magisk/KernelSU module zip or manually from `/data/vendor/fw/*.ko` with `chown root:root`, `chmod 644`, and `/system/bin/restorecon` (`u:object_r:vendor_data_file:s0`).

---

## Repository Structure & Driver Index

| Driver Stack | Directory | Kernel Modules / Binaries | CI Workflow | Documentation |
| :--- | :--- | :--- | :--- | :--- |
| **RTL8723DU USB Wi-Fi** | [`drivers/rtl8723du-wifi/`](drivers/rtl8723du-wifi/) | `rtl8723du.ko`, `cfg80211.ko`, `mac80211.ko` (STA + Monitor/Injection) | [`.github/workflows/build-rtl8723du-wifi.yml`](.github/workflows/build-rtl8723du-wifi.yml) | [Wi-Fi README](drivers/rtl8723du-wifi/README.md) |
| **RTL8723DU BLE / Bluetooth** | [`drivers/rtl8723du-bt/`](drivers/rtl8723du-bt/) | `btusb.ko`, `btrtl.ko`, `bt_core`, `btup` | [`.github/workflows/build-rtl8723du-bt.yml`](.github/workflows/build-rtl8723du-bt.yml) | [BT README](drivers/rtl8723du-bt/README.md) |
| **Hardened USB HID Gadget + FIDO2 Key** | [`drivers/usb-f-hid-fido2/`](drivers/usb-f-hid-fido2/) | `usb_f_hid_fix.ko` + `fido_touch_key.py` + `run_fido.sh` | [`.github/workflows/build-f-hid.yml`](.github/workflows/build-f-hid.yml) | [FIDO2 & `f_hid` Hardening Guide](docs/f_hid_hardening_and_fido2_guide.md) |
| **Generic GKI Auto-Builder** | [`drivers/rtl8723du-wifi/scripts/`](drivers/rtl8723du-wifi/scripts/) | `detect.sh` | [`.github/workflows/build-auto.yml`](.github/workflows/build-auto.yml) | [Automation Guide](drivers/rtl8723du-wifi/AUTOMATION.md) |

---

## How to Build Your Own `.ko` Modules on Any Rooted GKI Device

Modern Android GKI kernels (`5.10+`, `5.15+`, `6.1+`) enforce strict **KMI (Kernel Module Interface) CRC checks**, **Clang/LTO matching**, and **SELinux labeling**. You do **not** need a full ROM build—just an out-of-tree module compiled against the exact kernel commit and symbol table.

### Step 1: Collect Your Device's Exact Kernel Fingerprint
Run these commands as root (`su`) on your phone (via Termux, ADB, or SSH):

```sh
uname -r
cat /proc/version
# Check LTO & module signature enforcement
zcat /proc/config.gz | grep -E "CONFIG_LTO|CONFIG_MODVERSIONS|CONFIG_MODULE_SIG|CONFIG_KALLSYMS"
# Inspect a stock vendor .ko on your device for exact vermagic & Clang version
modinfo /vendor/lib/modules/*.ko | head -n 25
```

You can also run [`drivers/rtl8723du-wifi/scripts/detect.sh`](drivers/rtl8723du-wifi/scripts/detect.sh) to generate a complete device profile automatically.

### Step 2: Match KMI Symbols & `module_layout` CRC
1. **Exact kernel commit:** Use the AOSP GKI or OEM kernel commit matching `uname -r` / `/proc/version` (for example `5.15.153-android13-3-...`).
2. **Stage `Module.symvers`:** If your device's `vmlinux` / OEM kernel has vendor hooks or KMI variations, staging a reference `Module.symvers` (see `drivers/usb-f-hid-fido2/f_hid_fix/Module.symvers` and `.github/workflows/build-f-hid.yml`) guarantees matching CRCs (`module_layout`).
3. **Verify exported symbols on the live device:** Before calling any kernel helper in your C code, check `/proc/kallsyms` on the phone:
   ```sh
   su -c 'grep __ksymtab_<symbol_name> /proc/kallsyms'
   ```
   > Example from real hardware: `cdev_set_parent` existed in headers/`Module.symvers` during compilation, but was **not** exported in the live GKI 5.15 KMI (`Unknown symbol cdev_set_parent (err -2)`). Switching to KMI-exported `cdev_device_add()` resolved `insmod` immediately.

### Step 3: Compile via GitHub Actions (No Local 30 GB Kernel Tree Needed)
Building GKI modules locally requires downloading the full kernel source, Android Clang toolchain, and running `make modules_prepare`. The workflows in `.github/workflows/` automate this in ~1 minute (once cached):
- Fork this repository and enable **GitHub Actions**.
- Trigger the workflow you need from the **Actions** tab or via GitHub CLI:
  ```sh
  gh workflow run build-f-hid.yml --ref main
  ```
- Download either the raw `.ko` artifact or the flashable **Magisk / KernelSU** zip.

> **Workflow Naming Convention:** Parametrized workflows (`workflow_dispatch` with `kbranch`, `kcommit`, `clang_ver`, `btf`, `lto` inputs such as `build-rtl8723du-bt.yml`, `build-rtl8723du-wifi.yml`, `build-f-hid.yml`, and `build-auto.yml`) must **not** include specific device model names in their workflow filenames or `name:` titles; only non-parametrized, hard-coded workflows (such as `build.yml` for `UMS9230`) should include a device/SoC name.

### Step 4: Deploy & Load Safely on Rooted Android
For manual/persistent testing outside Magisk:
```sh
su -c '
  cp usb_f_hid_fix.ko /data/vendor/fw/usb_f_hid_fix.ko
  chown root:root /data/vendor/fw/usb_f_hid_fix.ko
  chmod 644 /data/vendor/fw/usb_f_hid_fix.ko
  /system/bin/restorecon /data/vendor/fw/usb_f_hid_fix.ko
  /system/bin/insmod /data/vendor/fw/usb_f_hid_fix.ko
'
```
If `insmod` fails, immediately check `dmesg | tail -n 30` for `Unknown symbol`, `disagrees about version of symbol`, or `avc: denied`.

---

## Building Modules Using a Coding Agent (Recommended Workflow)

Coding agents (Antigravity, Claude Code, Codex, Cursor, etc.) are remarkably effective at porting and debugging Android `.ko` modules **if you give them direct read/test access to the phone and a fast CI feedback loop**.

### Recommended Setup
1. **SSH / ADB access to the rooted phone:** Start `sshd` in Termux on the phone (e.g. port `22` or `8022`) with key-based auth and `su` (`Magisk` / `KernelSU`) enabled.
2. **GitHub CLI (`gh`) authenticated on your workstation:** Lets the agent push fixes, trigger `gh workflow run`, watch builds (`gh run watch`), and download `.ko` artifacts (`gh run download`) automatically.
3. **Clear safety boundaries:** Never let an agent blindly `rmmod` an untested module that failed registration or detach UDC inside a `kill`/`pkill` trap—read [`docs/AGENT-MODULE-BUILDING-GUIDE.md`](docs/AGENT-MODULE-BUILDING-GUIDE.md) for the exact checklist.

### Prompt Template to Kick Off a New Module Port
Copy and adapt this prompt for your coding agent:

```text
I want to build and load an out-of-tree <DRIVER_NAME> kernel module (.ko) for my rooted Android GKI device.
- Device access (termux): ssh -p 22 <user>@<phone-ip> (root via `su`) [OR] adb shell su (or a root adb daemon)
- Repo: <your-fork-url> (GitHub Actions workflow in .github/workflows/)

Please:
1. Inspect `uname -r`, `/proc/version`, `/proc/config.gz`, and `modinfo` on a stock `.ko` on the phone to determine exact KMI, LTO mode, and vermagic.
2. Check `/proc/kallsyms` for required `__ksymtab_*` symbols before editing/compiling.
3. Update the workflow/source, push, run the GitHub Actions build, download the `.ko`, and test `insmod` in `/data/vendor/fw/` (with `chown root:root`, `chmod 644`, `restorecon`).
4. Verify with `lsmod` and `dmesg`, and test clean `rmmod` + reload before finishing.
```

See [`docs/AGENT-MODULE-BUILDING-GUIDE.md`](docs/AGENT-MODULE-BUILDING-GUIDE.md) for full details on crash-dump triage (`kmsg` / `ramoops`), KMI debugging, and Android USB HAL / ConfigFS pitfalls.

---

## Additional Documentation
- [`docs/AGENT-MODULE-BUILDING-GUIDE.md`](docs/AGENT-MODULE-BUILDING-GUIDE.md) — End-to-end guide for building & debugging GKI `.ko` modules with coding agents
- [`docs/f_hid_hardening_and_fido2_guide.md`](docs/f_hid_hardening_and_fido2_guide.md) — Deep dive into `usb_f_hid_fix.ko`, CVE-2026-31606, UDC/HAL watchdog safety, and FIDO2 WebAuthn emulation
- [`docs/SAMSUNG-EXYNOS-CAMERA-ISSUE.md`](docs/SAMSUNG-EXYNOS-CAMERA-ISSUE.md) — Notes on Samsung Exynos USB/camera quirks
- [`LICENSE`](LICENSE) — Repository license

---

## Credits & Upstream References
- **[`markatsos/android-gki-usb-wifi`](https://github.com/markatsos/android-gki-usb-wifi)** — Original upstream project that pioneered building out-of-tree Android GKI USB Wi-Fi `.ko` modules via GitHub Actions against exact ACK commits, including [`build-auto.yml`](.github/workflows/build-auto.yml), [`detect.sh`](drivers/rtl8723du-wifi/scripts/detect.sh), and the Magisk/KernelSU module packaging layout upon which this repository's Wi-Fi, Bluetooth, and FIDO2 builds are based.
- **[`lwfinger/rtw88`](https://github.com/lwfinger/rtw88)** — Out-of-tree Realtek RTW88 driver source used by the RTL8723DU Wi-Fi and unified Wi-Fi + BT workflows.
