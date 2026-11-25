# Building Android GKI `.ko` Modules with Coding Agents

This guide captures the exact engineering workflow, pitfalls, and verification steps used to build and harden the drivers in this repository (`rtl8723du`, `btusb`/`btrtl`, and `usb_f_hid_fix`) on a rooted Android GKI device using a coding agent.

---

## 1. Environment Preparation

Give your coding agent three things so it can iterate end-to-end without manual copy-pasting:
1. **Remote shell to the phone:** Termux `sshd` (with SSH key auth) + root (`su 0` via KernelSU or Magisk).
2. **GitHub Actions CLI (`gh`):** Authenticated on your host machine so the agent can commit, push, trigger builds (`gh workflow run`), wait (`gh run watch --exit-status`), and pull artifacts (`gh run download`).
3. **A clean test script pattern:** On Windows PowerShell hosts, avoid nested quotes/pipes inside `ssh ... "su 0 -c '...'"`; instead, write a `.sh` script locally, `scp` it to the phone, run `sed -i 's/\r$//'`, and execute `su 0 /system/bin/sh /path/to/script.sh`.

---

## 2. Kernel Fingerprinting & KMI Matching

Before writing a single line of C or YAML, have the agent collect:
- `uname -r` and `/proc/version` (exact GKI version + Clang version)
- `zcat /proc/config.gz` (`CONFIG_LTO_CLANG_THIN` vs `CONFIG_LTO_CLANG_FULL` vs `CONFIG_LTO_NONE`, `CONFIG_MODVERSIONS`, `CONFIG_USB_CONFIGFS_F_HID`, etc.)
- `modinfo /vendor/lib/modules/<any_stock>.ko` (vermagic string)

### Common `insmod` Errors & Fixes
| Error | Kernel Cause (`dmesg`) | Fix |
| :--- | :--- | :--- |
| `Exec format error` (`ENOEXEC`) | `vermagic` or LTO mismatch | Match `LOCALVERSION`, Clang version, and `CONFIG_LTO_*` in CI workflow |
| `Invalid argument` (`EINVAL`) | `disagrees about version of symbol module_layout` | Stage the exact `Module.symvers` from the target GKI build/device |
| `No such file or directory` (`ENOENT`) | `Unknown symbol <sym> (err -2)` | Verify `grep __ksymtab_<sym> /proc/kallsyms` on the phone; replace non-KMI helpers (e.g. use `cdev_device_add` instead of `cdev_set_parent`) |
| `Permission denied` (`EACCES`) | SELinux blocked `finit_module` from `/data/local/tmp` or Termux home | Move `.ko` to `/data/vendor/fw/`, `chown root:root`, `chmod 644`, `/system/bin/restorecon` |

---

## 3. Android-Specific Stability Traps (Learned from Hardware Crash Dumps)

1. **Never toggle ConfigFS `UDC` in `pkill`/exit traps:**
   Android's USB HAL (`vendor.samsung.hardware.usb-service` / `android.hardware.usb-service`) monitors `sys.usb.config` and `UDC`. Unbinding `UDC` behind the HAL's back while `adbd`/`mtp` are active can trigger a USB HAL watchdog reboot (`SEC_REBOOT`). Bind the gadget function once and only unbind on explicit user teardown.
2. **Expect automatic UDC unbind/rebind on USB selective suspend:**
   Vendor USB monitors (such as Samsung `ss_mon.mtp`) unbind and rebind the gadget UDC automatically when the host suspends the port—even while userspace holds `/dev/hidg0` open. Any driver that calls `cdev_init()` / `cdev_device_add()` inside `bind()` instead of `alloc()` will corrupt `cdev->list` and panic in `cd_forget()` on `Ctrl+C` (CVE-2026-31606).
3. **Guard `hidg_modexit()` against `-EEXIST` aliases:**
   If a module registers multiple function aliases (`hid_fix`, `f_hid`, `hid`) and one returns `-EEXIST` (because stock `hid` is built-in), `usb_function_unregister()` during `rmmod` will panic with `list_del corruption` unless each registration's success is tracked with a boolean flag.

---

## 4. Mandatory Verification Checklist Before Finishing

Every `.ko` change should pass this sequence on the real device:
1. `insmod` succeeds and `lsmod` lists the module.
2. `dmesg` shows zero warnings/errors.
3. Userspace consumer opens the device node, survives a simulated UDC unbind/rebind (or cable unplug/replug), and exits cleanly on `SIGINT`.
4. Clean `rmmod` succeeds without `list_del` / refcount panics, followed by clean `insmod` reload.
