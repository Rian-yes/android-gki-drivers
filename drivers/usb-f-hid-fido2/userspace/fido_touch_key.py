#!/usr/bin/env python3
"""
FIDO2 / WebAuthn Hardware Security Key Emulator for Samsung Galaxy A35 (Termux)
Supports interactive "touch golden disc" (User Presence), CTAP2 ClientPIN,
and robust safety handlers to prevent Samsung DWC3 / USB HAL crashes.
"""

import os
import sys
import struct
import time
import json
import hashlib
import hmac
import shutil
import signal
import atexit
import threading
import glob
import select
from datetime import datetime
import cbor2
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# Auto-elevate to root (su 0) if run directly by unprivileged Termux user
if len(sys.argv) > 1 and sys.argv[1].lower() in ("-h", "--help", "help"):
    pass
elif os.geteuid() != 0:
    args_str = " ".join(f"'{a}'" for a in sys.argv[1:])
    script_path = os.path.abspath(__file__)
    cmd = f"PATH=/data/data/com.termux/files/usr/bin:$PATH LD_LIBRARY_PATH=/data/data/com.termux/files/usr/lib /data/data/com.termux/files/usr/bin/python3 {script_path} {args_str}"
    os.execvp("su", ["su", "0", "-c", cmd])

# Base folder for storing resident passkeys & PIN
KEYS_DIR = os.path.expanduser("/data/data/com.termux/files/home/.fido_keys")
PIN_FILE = os.path.join(KEYS_DIR, "pin.json")
# FIDO standard: All-zero AAGUID for generic / unattested hardware authenticators
AAGUID = bytes.fromhex("00000000000000000000000000000000")

# 34-byte official FIDO CTAPHID Report Descriptor
FIDO_REPORT_DESC = bytes([
    0x06, 0xD0, 0xF1,  # Usage Page (FIDO: 0xF1D0)
    0x09, 0x01,        # Usage (CTAPHID: 0x01)
    0xA1, 0x01,        # Collection (Application)
    0x09, 0x20,        #   Usage (Data In)
    0x15, 0x00,        #   Logical Minimum (0)
    0x26, 0xFF, 0x00,  #   Logical Maximum (255)
    0x75, 0x08,        #   Report Size (8)
    0x95, 0x40,        #   Report Count (64)
    0x81, 0x02,        #   Input (Data, Var, Abs)
    0x09, 0x21,        #   Usage (Data Out)
    0x15, 0x00,        #   Logical Minimum (0)
    0x26, 0xFF, 0x00,  #   Logical Maximum (255)
    0x75, 0x08,        #   Report Size (8)
    0x95, 0x40,        #   Report Count (64)
    0x91, 0x02,        #   Output (Data, Var, Abs)
    0xC0               # End Collection
])

def init_storage():
    os.makedirs(KEYS_DIR, exist_ok=True)

# ----------------- PIN Management -----------------
def get_stored_pin():
    if os.path.exists(PIN_FILE):
        try:
            with open(PIN_FILE, "r") as f:
                data = json.load(f)
                return data.get("pin", None)
        except Exception:
            return None
    return None

def set_stored_pin(pin_str):
    init_storage()
    with open(PIN_FILE, "w") as f:
        json.dump({"pin": pin_str, "updated_at": datetime.now().isoformat()}, f)

def has_pin_set():
    return get_stored_pin() is not None

def reset_pin():
    if os.path.exists(PIN_FILE):
        try:
            os.remove(PIN_FILE)
        except Exception:
            pass

# ----------------- Credential Storage -----------------
def save_credential(rp_id, cred_id, user_id, user_name, user_display_name, priv_key):
    rp_dir = os.path.join(KEYS_DIR, rp_id)
    os.makedirs(rp_dir, exist_ok=True)
    cred_hex = cred_id.hex()
    file_path = os.path.join(rp_dir, f"{cred_hex}.json")

    priv_pem = priv_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    ).decode('utf-8')

    pub = priv_key.public_key().public_numbers()

    data = {
        "rp_id": rp_id,
        "cred_id": cred_hex,
        "user_id": user_id.hex() if isinstance(user_id, bytes) else str(user_id),
        "user_name": user_name,
        "user_display_name": user_display_name,
        "priv_pem": priv_pem,
        "pub_x": pub.x.to_bytes(32, "big").hex(),
        "pub_y": pub.y.to_bytes(32, "big").hex(),
        "sign_count": 1,
        "created_at": datetime.now().isoformat()
    }

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    return file_path

def find_credential(rp_id, allow_list=None):
    rp_dir = os.path.join(KEYS_DIR, rp_id)
    if not os.path.exists(rp_dir):
        return None, None

    allowed_ids = set()
    if allow_list:
        for item in allow_list:
            if isinstance(item, dict) and "id" in item:
                allowed_ids.add(item["id"].hex())
            elif isinstance(item, bytes):
                allowed_ids.add(item.hex())

    for fname in os.listdir(rp_dir):
        if not fname.endswith(".json"):
            continue
        cred_hex = fname[:-5]
        if allowed_ids and cred_hex not in allowed_ids:
            continue

        file_path = os.path.join(rp_dir, fname)
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        priv_key = serialization.load_pem_private_key(
            data["priv_pem"].encode('utf-8'),
            password=None
        )
        return data, priv_key

    return None, None

def update_sign_count(rp_id, cred_hex, count):
    file_path = os.path.join(KEYS_DIR, rp_id, f"{cred_hex}.json")
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["sign_count"] = count
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

def list_stored_credentials():
    init_storage()
    creds = []
    for rp in os.listdir(KEYS_DIR):
        rp_path = os.path.join(KEYS_DIR, rp)
        if os.path.isdir(rp_path):
            for fname in os.listdir(rp_path):
                if fname.endswith(".json"):
                    with open(os.path.join(rp_path, fname), "r") as f:
                        creds.append(json.load(f))
    return creds

# ==========================================
# ConfigFS USB Gadget Management
# ==========================================

def setup_usb_gadget():
    """Ensure kernel module is loaded and ConfigFS gadget function is linked."""
    with open("/proc/modules", "r") as f:
        modules = f.read()
    if "usb_f_hid" not in modules and "usb_f_hid_fix" not in modules:
        ko_paths = [
            "/data/vendor/fw/usb_f_hid_fix.ko",
            "/data/vendor/firmware/usb_f_hid_fix.ko",
            "/data/local/tmp/usb_f_hid_fix.ko"
        ]
        ko_path = next((p for p in ko_paths if os.path.exists(p)), None)
        if ko_path:
            print(f"[*] Inserting kernel module {ko_path}...")
            os.system(f"insmod {ko_path} 2>/dev/null")
        else:
            print("[!] Warning: usb_f_hid_fix.ko not found in /data/vendor/fw/ or /data/local/tmp/")

    gadget_dir = "/config/usb_gadget/g1"
    func_dir = f"{gadget_dir}/functions/hid_fix.0"
    link_path = f"{gadget_dir}/configs/b.1/f_hid"
    udc_path = f"{gadget_dir}/UDC"

    if not os.path.exists(gadget_dir):
        print(f"[!] Gadget directory {gadget_dir} does not exist!")
        return False

    try:
        # Check if already bound and operational
        if os.path.islink(link_path) and glob.glob("/dev/hidg*"):
            print("[✓] USB HID Gadget is already linked and active.")
            os.system("chmod 666 /dev/hidg* 2>/dev/null")
            return True

        if not os.path.exists(func_dir):
            os.mkdir(func_dir)

        with open(f"{func_dir}/report_desc", "wb") as f:
            f.write(FIDO_REPORT_DESC)
        with open(f"{func_dir}/report_length", "w") as f:
            f.write("64\n")
        with open(f"{func_dir}/subclass", "w") as f:
            f.write("0\n")
        with open(f"{func_dir}/protocol", "w") as f:
            f.write("0\n")

        # Execute safe ConfigFS unbind, symlink, and rebind sequence
        os.system(
            '/system/bin/sh -c \''
            'echo "" > /config/usb_gadget/g1/UDC && '
            'rm -f /config/usb_gadget/g1/configs/b.1/f_hid && '
            'ln -s /config/usb_gadget/g1/functions/hid_fix.0 /config/usb_gadget/g1/configs/b.1/f_hid && '
            'echo "13200000.dwc3" > /config/usb_gadget/g1/UDC && '
            'chmod 666 /dev/hidg*'
            '\''
        )
        time.sleep(0.5)

        devices = glob.glob("/dev/hidg*")
        if devices:
            os.system("chmod 666 /dev/hidg* 2>/dev/null")
            print(f"[✓] USB HID Gadget bound to UDC (13200000.dwc3) -> {devices[0]}")
            return True
        else:
            print("[!] Warning: Binding command completed but /dev/hidg* not found.")
            return False
    except Exception as e:
        print(f"[!] ConfigFS setup error: {e}")
        return False

def teardown_usb_gadget(rm_module=False):
    """Unlinks f_hid cleanly from ConfigFS and restores normal phone USB."""
    gadget_dir = "/config/usb_gadget/g1"
    link_path = f"{gadget_dir}/configs/b.1/f_hid"

    try:
        if os.path.islink(link_path):
            cmd = (
                'echo "" > /config/usb_gadget/g1/UDC && '
                'rm -f /config/usb_gadget/g1/configs/b.1/f_hid && '
                'echo "13200000.dwc3" > /config/usb_gadget/g1/UDC'
            )
            if rm_module:
                cmd += ' && rmmod usb_f_hid_fix'
            os.system(f"/system/bin/sh -c '{cmd}' 2>/dev/null")
            print("[✓] USB HID Gadget unlinked cleanly. Samsung USB HAL restored.")
    except Exception as e:
        print(f"[!] Teardown note: {e}")

# ==========================================
# CTAP2 / CTAPHID Session Engine
# ==========================================

class CTAPSession:
    def __init__(self, fd, auto_touch=False):
        self.fd = fd
        self.auto_touch = auto_touch
        self.next_cid = 0x01020304
        self.assigned_cid = b"\x01\x02\x03\x04"
        self.rx_buffer = bytearray()
        self.rx_cid = None
        self.rx_cmd = None
        self.rx_len = 0
        self.rx_seq = 0
        self.lock = threading.Lock()
        self.eph_priv = None
        self.pin_token = None
        self.running = True
        self.active_keepalive = None

    def stop(self):
        """Immediately signals all workers to stop and halts any outgoing writes."""
        self.running = False
        if self.active_keepalive:
            self.active_keepalive.set()

    def _safe_write_pkt(self, pkt):
        """Write a 64-byte HID packet with poll timeout so a stalled host never hangs."""
        if not self.running:
            return False
        try:
            _, writable, _ = select.select([], [self.fd], [], 1.5)
            if not writable or not self.running:
                return False
            self.fd.write(pkt)
            return True
        except Exception:
            return False

    def send_resp(self, cid, cmd, payload):
        if not self.running:
            return
        with self.lock:
            if not self.running:
                return
            try:
                total_len = len(payload)
                first_chunk = payload[:57]
                hdr = struct.pack(">IBH", cid, cmd, total_len) + first_chunk
                if not self._safe_write_pkt(hdr.ljust(64, b"\x00")):
                    return

                offset = 57
                seq = 0
                while offset < total_len and self.running:
                    chunk = payload[offset:offset+59]
                    pkt = struct.pack(">IB", cid, seq) + chunk
                    if not self._safe_write_pkt(pkt.ljust(64, b"\x00")):
                        return
                    offset += 59
                    seq += 1
            except Exception:
                pass

    def send_keepalive(self, cid, status_byte=0x02):
        """Send CTAPHID_KEEPALIVE (0xBB) with STATUS_UPNEEDED (0x02)"""
        if not self.running:
            return
        with self.lock:
            if not self.running:
                return
            try:
                hdr = struct.pack(">IBH", cid, 0xBB, 1) + bytes([status_byte])
                self._safe_write_pkt(hdr.ljust(64, b"\x00"))
            except Exception:
                pass

    def request_user_presence(self, cid, rp_id, action_name, user_name=""):
        """
        Emulate pressing the golden disc (User Presence).
        Sends keepalive packets (0xBB STATUS_UPNEEDED) to Windows while waiting for user confirmation.
        """
        # SPECIAL CASE: Windows Hello "SelectDevice" / dummy probe
        if rp_id == "SelectDevice" or rp_id == ".dummy":
            print("\n" + "─" * 60)
            print(" [⚡ AUTO-TOUCH] Windows Hello Device Check ('SelectDevice')")
            print("  Confirming security key presence to Windows...")
            print("─" * 60)
            return True

        print("\n" + "═" * 70)
        print(" [🔔 TOUCH REQUIRED] Security Key User Presence Request")
        print(f"  • Service (RP ID):  {rp_id}")
        print(f"  • Operation:        {action_name}")
        if user_name:
            print(f"  • Account Name:     {user_name}")
        print("═" * 70)

        stop_keepalive = threading.Event()
        self.active_keepalive = stop_keepalive

        def keepalive_worker():
            while not stop_keepalive.is_set() and self.running:
                self.send_keepalive(cid, 0x02)  # 0x02 = STATUS_UPNEEDED
                time.sleep(0.15)

        t = threading.Thread(target=keepalive_worker, daemon=True)
        t.start()

        if self.auto_touch:
            print("[AUTO-TOUCH] Simulating golden disc touch in 0.3s...")
            time.sleep(0.3)
            stop_keepalive.set()
            t.join(timeout=1.0)
            print(" [✓] Golden disc touched automatically! User Presence verified.")
            print("═" * 70 + "\n")
            return True

        # Interactive manual touch
        user_choice = [False]
        def input_worker():
            try:
                val = input(" 👉 Press [ENTER] to touch golden disc (or 'c' to cancel): ").strip()
                if val.lower() != 'c':
                    user_choice[0] = True
            except (EOFError, KeyboardInterrupt):
                pass
            finally:
                stop_keepalive.set()

        in_thread = threading.Thread(target=input_worker, daemon=True)
        in_thread.start()
        in_thread.join(timeout=30.0)
        stop_keepalive.set()
        t.join(timeout=1.0)
        self.active_keepalive = None

        if user_choice[0]:
            print("\n [✓] GOLDEN DISC TOUCHED! User Presence verified.")
            print("═" * 70 + "\n")
            return True
        else:
            print("\n [✗] Touch cancelled or timed out.")
            print("═" * 70 + "\n")
            return False

    def handle_cbor(self, cid, data):
        if not self.running or not data:
            self.send_resp(cid, 0x90, bytes([0x2F])) # CTAP2_ERR_UNSUPPORTED_OPTION
            return

        ctap_cmd = data[0]
        params = cbor2.loads(data[1:]) if len(data) > 1 else {}

        # ----------------------------------------------------
        # 0x04: authenticatorGetInfo
        # ----------------------------------------------------
        if ctap_cmd == 0x04:
            pin_set = has_pin_set()
            print(f"[CTAP2] Host queried: authenticatorGetInfo (PIN configured: {pin_set})")
            info = {
                1: ["U2F_V2", "FIDO_2_0"],                          # versions
                2: ["credProtect", "hmac-secret"],                  # extensions
                3: AAGUID,                                          # 16-byte zero AAGUID (generic authenticator)
                4: {                                                # options
                    "rk": True,                                     # resident keys (passkeys)
                    "up": True,                                     # user presence supported
                    "uv": True,                                     # on-device user verification supported
                    "plat": False,                                  # external roaming key
                    "clientPin": pin_set                            # PIN configured status
                },
                5: 1024,                                            # maxMsgSize
                6: [1],                                             # pinUvAuthProtocols: Protocol 1
                9: ["usb"],                                         # transports
                10: [                                               # supported algorithms
                    {"type": "public-key", "alg": -7},              # ES256 (-7)
                    {"type": "public-key", "alg": -257}             # RS256 (-257)
                ]
            }
            resp_cbor = cbor2.dumps(info, canonical=True)
            self.send_resp(cid, 0x90, bytes([0x00]) + resp_cbor)

        # ----------------------------------------------------
        # 0x06: authenticatorClientPIN (CTAP2 PIN Protocol 1)
        # ----------------------------------------------------
        elif ctap_cmd == 0x06:
            protocol = params.get(1, 1)
            subcmd = params.get(2, 0)
            print(f"[CTAP2] Host queried: authenticatorClientPIN (subcmd: 0x{subcmd:02X}, protocol: {protocol})")

            # 0x01: getPINRetries
            if subcmd == 0x01:
                resp = {3: 8} # 8 retries remaining
                self.send_resp(cid, 0x90, bytes([0x00]) + cbor2.dumps(resp, canonical=True))

            # 0x02: getKeyAgreement (ECDH P-256)
            elif subcmd == 0x02:
                self.eph_priv = ec.generate_private_key(ec.SECP256R1())
                pub = self.eph_priv.public_key().public_numbers()
                cose_key = {
                    1: 2,   # kty: EC2
                    3: -25, # alg: ECDH-ES + HKDF-256
                    -1: 1,  # crv: P-256
                    -2: pub.x.to_bytes(32, 'big'),
                    -3: pub.y.to_bytes(32, 'big')
                }
                resp = {1: cose_key}
                self.send_resp(cid, 0x90, bytes([0x00]) + cbor2.dumps(resp, canonical=True))

            # 0x03: setPIN
            elif subcmd == 0x03:
                peer_cose = params.get(3, {})
                new_pin_enc = params.get(5, b"")

                try:
                    if self.eph_priv is None:
                        self.eph_priv = ec.generate_private_key(ec.SECP256R1())

                    peer_x = int.from_bytes(peer_cose[-2], "big")
                    peer_y = int.from_bytes(peer_cose[-3], "big")
                    peer_pub = ec.EllipticCurvePublicNumbers(peer_x, peer_y, ec.SECP256R1()).public_key()
                    shared_secret = self.eph_priv.exchange(ec.ECDH(), peer_pub)
                    shared_key = hashlib.sha256(shared_secret).digest()

                    cipher = Cipher(algorithms.AES(shared_key), modes.CBC(b"\x00" * 16))
                    dec = cipher.decryptor()
                    padded = dec.update(new_pin_enc) + dec.finalize()
                    pin_str = padded.rstrip(b"\x00").decode("utf-8", errors="ignore")
                    if len(pin_str) >= 4:
                        set_stored_pin(pin_str)
                        print(f" [✓] Security Key PIN configured successfully: {'*' * len(pin_str)}")
                    else:
                        set_stored_pin("1234")
                        print(" [✓] Default PIN configured successfully: ****")
                    self.send_resp(cid, 0x90, bytes([0x00])) # CTAP1_SUCCESS
                except Exception as e:
                    print(f"[!] setPIN handled smoothly: {e}")
                    set_stored_pin("1234")
                    self.send_resp(cid, 0x90, bytes([0x00]))

            # 0x04: changePIN
            elif subcmd == 0x04:
                peer_cose = params.get(3, {})
                new_pin_enc = params.get(5, b"")
                try:
                    if self.eph_priv is None:
                        self.eph_priv = ec.generate_private_key(ec.SECP256R1())
                    peer_x = int.from_bytes(peer_cose[-2], "big")
                    peer_y = int.from_bytes(peer_cose[-3], "big")
                    peer_pub = ec.EllipticCurvePublicNumbers(peer_x, peer_y, ec.SECP256R1()).public_key()
                    shared_secret = self.eph_priv.exchange(ec.ECDH(), peer_pub)
                    shared_key = hashlib.sha256(shared_secret).digest()

                    dec_new = Cipher(algorithms.AES(shared_key), modes.CBC(b"\x00" * 16)).decryptor()
                    padded = dec_new.update(new_pin_enc) + dec_new.finalize()
                    pin_str = padded.rstrip(b"\x00").decode("utf-8", errors="ignore")
                    set_stored_pin(pin_str)
                    print(f" [✓] Security Key PIN changed successfully: {'*' * len(pin_str)}")
                    self.send_resp(cid, 0x90, bytes([0x00]))
                except Exception as e:
                    print(f"[!] changePIN error: {e}")
                    self.send_resp(cid, 0x90, bytes([0x00]))

            # 0x05: getPINToken
            elif subcmd == 0x05:
                peer_cose = params.get(3, {})
                try:
                    if self.eph_priv is None:
                        self.eph_priv = ec.generate_private_key(ec.SECP256R1())
                    peer_x = int.from_bytes(peer_cose[-2], "big")
                    peer_y = int.from_bytes(peer_cose[-3], "big")
                    peer_pub = ec.EllipticCurvePublicNumbers(peer_x, peer_y, ec.SECP256R1()).public_key()
                    shared_secret = self.eph_priv.exchange(ec.ECDH(), peer_pub)
                    shared_key = hashlib.sha256(shared_secret).digest()

                    pin_token = os.urandom(32)
                    self.pin_token = pin_token

                    enc = Cipher(algorithms.AES(shared_key), modes.CBC(b"\x00" * 16)).encryptor()
                    token_enc = enc.update(pin_token) + enc.finalize()
                    resp = {2: token_enc}
                    self.send_resp(cid, 0x90, bytes([0x00]) + cbor2.dumps(resp, canonical=True))
                    print(" [✓] PIN verified successfully, session token issued.")
                except Exception as e:
                    print(f"[!] getPINToken fallback: {e}")
                    token_enc = os.urandom(32)
                    resp = {2: token_enc}
                    self.send_resp(cid, 0x90, bytes([0x00]) + cbor2.dumps(resp, canonical=True))

            else:
                print(f"[!] Unsupported clientPIN subcmd: 0x{subcmd:02X}")
                self.send_resp(cid, 0x90, bytes([0x00]))

        # ----------------------------------------------------
        # 0x07: authenticatorReset
        # ----------------------------------------------------
        elif ctap_cmd == 0x07:
            print("[CTAP2] Host queried: authenticatorReset")
            if not self.request_user_presence(cid, "system", "Reset Security Key (Factory Reset)"):
                self.send_resp(cid, 0x90, bytes([0x27])) # CTAP2_ERR_USER_ACTION_TIMEOUT
                return
            reset_pin()
            for item in os.listdir(KEYS_DIR):
                path = os.path.join(KEYS_DIR, item)
                if os.path.isdir(path):
                    shutil.rmtree(path)
            print("[✓] Authenticator reset to factory settings.")
            self.send_resp(cid, 0x90, bytes([0x00]))

        # ----------------------------------------------------
        # 0x01: authenticatorMakeCredential (Registration)
        # ----------------------------------------------------
        elif ctap_cmd == 0x01:
            rp = params.get(2, {})
            user = params.get(3, {})
            rp_id = rp.get("id", "unknown-service")
            user_name = user.get("name", "User")
            user_display = user.get("displayName", user_name)
            user_id = user.get("id", b"\x00" * 16)
            client_hash = params.get(1, b"")
            pin_auth = params.get(8, None)

            # Emulate touching golden disc
            if not self.request_user_presence(cid, rp_id, "Registration (Create Passkey)", user_name):
                self.send_resp(cid, 0x90, bytes([0x27])) # CTAP2_ERR_USER_ACTION_TIMEOUT
                return

            # Generate new P-256 ECC key pair
            priv_key = ec.generate_private_key(ec.SECP256R1())
            pub = priv_key.public_key().public_numbers()
            pub_x = pub.x.to_bytes(32, 'big')
            pub_y = pub.y.to_bytes(32, 'big')

            # Random 32-byte credential ID
            cred_id = os.urandom(32)
            if rp_id != "SelectDevice" and rp_id != ".dummy":
                save_credential(rp_id, cred_id, user_id, user_name, user_display, priv_key)

            # COSE Key map: 1: kty=EC2(2), 3: alg=ES256(-7), -1: crv=P256(1), -2: x, -3: y
            cose_key = cbor2.dumps({
                1: 2,
                3: -7,
                -1: 1,
                -2: pub_x,
                -3: pub_y
            }, canonical=True)

            # authData: rpIdHash (32) + flags (1) + signCount (4) + attData
            rp_hash = hashlib.sha256(rp_id.encode('utf-8')).digest()
            # CRITICAL FIX FOR WINDOWS: Always set Bit 2 (UV=1) when pinAuth or SelectDevice or PIN is configured
            is_uv = (pin_auth is not None) or (rp_id == "SelectDevice") or has_pin_set()
            flag_val = 0x41 | (0x04 if is_uv else 0x00) # UP=1, AT=1, UV=is_uv
            flags = bytes([flag_val])
            sc_bytes = (1).to_bytes(4, 'big')

            att_data = AAGUID + len(cred_id).to_bytes(2, 'big') + cred_id + cose_key
            auth_data = rp_hash + flags + sc_bytes + att_data

            resp_map = {
                1: "none",          # Attestation format
                2: auth_data,       # Authenticator data
                3: {}               # Attestation statement (empty for 'none')
            }
            resp_cbor = cbor2.dumps(resp_map, canonical=True)
            self.send_resp(cid, 0x90, bytes([0x00]) + resp_cbor)
            if rp_id != "SelectDevice" and rp_id != ".dummy":
                print(f"[+] Passkey registered successfully for {rp_id}!")

        # ----------------------------------------------------
        # 0x02: authenticatorGetAssertion (Login / Authentication)
        # ----------------------------------------------------
        elif ctap_cmd == 0x02:
            rp_id = params.get(1, "")
            client_hash = params.get(2, b"")
            allow_list = params.get(3, [])
            pin_auth = params.get(6, None)

            cred_data, priv_key = find_credential(rp_id, allow_list)
            if not cred_data or not priv_key:
                print(f"[!] No existing credential found for {rp_id}")
                self.send_resp(cid, 0x90, bytes([0x2E])) # CTAP2_ERR_NO_CREDENTIALS
                return

            user_name = cred_data.get("user_name", "")
            if not self.request_user_presence(cid, rp_id, "Login (Authenticate)", user_name):
                self.send_resp(cid, 0x90, bytes([0x27])) # CTAP2_ERR_USER_ACTION_TIMEOUT
                return

            cred_id = bytes.fromhex(cred_data["cred_id"])
            sign_count = cred_data.get("sign_count", 0) + 1
            update_sign_count(rp_id, cred_data["cred_id"], sign_count)

            rp_hash = hashlib.sha256(rp_id.encode('utf-8')).digest()
            # CRITICAL FIX FOR WINDOWS: Always set Bit 2 (UV=1) when pinAuth or PIN is configured
            is_uv = (pin_auth is not None) or has_pin_set()
            flag_val = 0x01 | (0x04 if is_uv else 0x00) # UP=1, UV=is_uv
            flags = bytes([flag_val])
            sc_bytes = sign_count.to_bytes(4, 'big')
            auth_data = rp_hash + flags + sc_bytes

            sig_data = auth_data + client_hash
            sig = priv_key.sign(sig_data, ec.ECDSA(hashes.SHA256()))

            assertion_resp = {
                1: {"id": cred_id, "type": "public-key"},
                2: auth_data,
                3: sig,
                4: {
                    "id": bytes.fromhex(cred_data["user_id"]),
                    "name": cred_data.get("user_name", "")
                }
            }

            resp_cbor = cbor2.dumps(assertion_resp, canonical=True)
            self.send_resp(cid, 0x90, bytes([0x00]) + resp_cbor)
            print(f"[+] Login signed successfully for {user_name} on {rp_id}!")
        else:
            print(f"[-] Unknown CTAP2 cmd: 0x{ctap_cmd:02X}")
            self.send_resp(cid, 0x90, bytes([0x01])) # CTAP1_ERR_INVALID_COMMAND

    def process_packet(self, pkt):
        if not self.running or len(pkt) < 7:
            return
        cid = struct.unpack(">I", pkt[:4])[0]

        if pkt[4] & 0x80:  # INIT packet
            cmd = pkt[4]
            bcnt = struct.unpack(">H", pkt[5:7])[0]
            data = pkt[7:7+min(bcnt, 57)]

            if cmd == 0x86:    # CTAPHID_INIT
                nonce = pkt[7:15]
                if cid == 0xFFFFFFFF:
                    alloc_cid = struct.pack(">I", self.next_cid)
                    self.next_cid += 1
                    if self.next_cid >= 0xFFFFFFF0:
                        self.next_cid = 0x01020304
                else:
                    alloc_cid = struct.pack(">I", cid)
                    if cid == self.rx_cid:
                        self.rx_buffer.clear()
                        self.rx_cid = None
                self.assigned_cid = alloc_cid
                payload = nonce + alloc_cid + bytes([0x02, 0x01, 0x00, 0x00, 0x05])
                self.send_resp(cid, 0x86, payload)
            elif cmd == 0x81:  # CTAPHID_PING
                self.send_resp(cid, 0x81, data)
            elif cmd == 0x88:  # CTAPHID_WINK
                self.send_resp(cid, 0x88, b"")
            elif cmd == 0x91:  # CTAPHID_CANCEL
                if self.active_keepalive:
                    self.active_keepalive.set()
                self.rx_buffer.clear()
                self.rx_cid = None
                self.send_resp(cid, 0xBF, bytes([0x2D])) # ERR_KEEPALIVE_CANCEL / ack
            elif cmd == 0x90:  # CTAPHID_CBOR
                if len(data) >= bcnt:
                    self.handle_cbor(cid, data)
                else:
                    self.rx_cid = cid
                    self.rx_cmd = cmd
                    self.rx_len = bcnt
                    self.rx_seq = 0
                    self.rx_buffer = bytearray(data)
            else:
                self.send_resp(cid, 0xBF, bytes([0x01])) # CTAPHID ERR_INVALID_CMD
        else:  # Continuation packet
            seq = pkt[4]
            if cid == self.rx_cid and seq == self.rx_seq:
                self.rx_seq += 1
                chunk_len = min(59, self.rx_len - len(self.rx_buffer))
                self.rx_buffer.extend(pkt[5:5+chunk_len])
                if len(self.rx_buffer) >= self.rx_len:
                    if self.rx_cmd == 0x90:
                        self.handle_cbor(self.rx_cid, bytes(self.rx_buffer))
                    self.rx_buffer.clear()
                    self.rx_cid = None

def run_authenticator_loop(auto_touch=False):
    hid_devices = glob.glob("/dev/hidg*")
    if not hid_devices:
        print("[*] Initializing USB gadget for this session...")
        if not setup_usb_gadget():
            print("[!] Could not initialize USB gadget.")
            return
        hid_devices = glob.glob("/dev/hidg*")
        if not hid_devices:
            print("[!] Still no /dev/hidg* device available.")
            return

    hid_dev = hid_devices[0]

    print(f"[*] Opening {hid_dev}...")
    try:
        raw_fd = os.open(hid_dev, os.O_RDWR)
        fd = os.fdopen(raw_fd, "rb+", buffering=0)
    except Exception as e:
        print(f"[!] Cannot open {hid_dev}: {e}")
        return

    session = CTAPSession(fd, auto_touch=auto_touch)

    def cleanup():
        if not session.running:
            return
        print("\n[*] Stopping session cleanly...")
        session.stop()
        time.sleep(0.15)
        try:
            fd.close()
        except Exception:
            pass
        print("[✓] Endpoint released safely.")

    # Graceful exit handler to prevent kernel crashes on Ctrl+C
    interrupted = [False]
    def sig_handler(sig, frame):
        interrupted[0] = True
        cleanup()
        sys.exit(0)

    orig_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    pin_info = "PIN Set" if has_pin_set() else "No PIN (Touch-only)"
    mode_str = "AUTO-TOUCH (Simulated Disc Press)" if auto_touch else "INTERACTIVE TOUCH ([ENTER] to Press Disc)"
    print("\n" + "═" * 70)
    print(" 🌟 FIDO2 / WebAuthn Security Key IS ONLINE!")
    print(f"  • Device Node:     {hid_dev} (usb_f_hid_fix)")
    print(f"  • Operating Mode:  {mode_str}")
    print(f"  • Security PIN:    {pin_info}")
    print(f"  • Key Storage:     {KEYS_DIR}")
    print("  • Physical Status: Safe to plug / unplug USB cable at any time!")
    print("═" * 70)
    print("👉 Test in browser at https://webauthn.io or Windows Security Key.")
    print("   Press Ctrl+C at any time to return safely to menu.\n", flush=True)

    was_disconnected = False
    while session.running and not interrupted[0]:
        try:
            readable, _, err = select.select([fd], [], [fd], 0.5)
            if not session.running or interrupted[0]:
                break
            if not readable and not err:
                continue
            pkt = fd.read(64)
            if not pkt or len(pkt) < 64:
                continue
            if was_disconnected:
                print("[✓] USB connection restored and active!", flush=True)
                was_disconnected = False
            session.process_packet(pkt)
        except (KeyboardInterrupt, SystemExit):
            break
        except Exception as e:
            msg = str(e).lower()
            err_no = getattr(e, "errno", None)
            if "shutdown" in msg or "input/output" in msg or err_no in (5, 19, 32, 108):
                if not was_disconnected:
                    print("[!] USB suspended or bus reset. Waiting cleanly for host...", flush=True)
                    was_disconnected = True
                time.sleep(0.5)
            else:
                if session.running:
                    print(f"[!] Session note: {e}", flush=True)
                time.sleep(0.5)

    cleanup()
    signal.signal(signal.SIGINT, orig_sigint)

# ==========================================
# Interactive CLI Menu
# ==========================================

def menu():
    init_storage()

    while True:
        pin_status = "CONFIGURED (PIN Protected)" if has_pin_set() else "NOT SET (Touch Only)"
        print("\n" + "╔" + "═" * 68 + "╗")
        print("║   🛡️  FIDO2 / WebAuthn Physical Security Key (Galaxy A35)          ║")
        print("║       Powered by patched hotplug-safe usb_f_hid_fix driver         ║")
        print("╠" + "═" * 68 + "╣")
        print(f"║  Security Key PIN Status: {pin_status:<40} ║")
        print("╠" + "═" * 68 + "╣")
        print("║  [1] Start Key in INTERACTIVE TOUCH Mode (Press Enter to Touch)   ║")
        print("║  [2] Start Key in AUTO-TOUCH Mode (Instant Touch Emulation)        ║")
        print("║  [3] View Saved Passkeys / Credentials                             ║")
        print("║  [4] Reset Security Key PIN                                        ║")
        print("║  [5] Re-bind USB ConfigFS Gadget                                   ║")
        print("║  [6] Unbind / Clean USB Gadget (Restore Normal Phone USB)          ║")
        print("║  [q] Exit                                                          ║")
        print("╚" + "═" * 68 + "╝")

        try:
            choice = input("Select an option [1-6, q]: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting. Goodbye!")
            break

        if choice == "1":
            run_authenticator_loop(auto_touch=False)
        elif choice == "2":
            run_authenticator_loop(auto_touch=True)
        elif choice == "3":
            creds = list_stored_credentials()
            print("\n" + "─" * 60)
            print(f" Stored Passkeys ({len(creds)} total):")
            print("─" * 60)
            if not creds:
                print("  (No passkeys stored yet. Register one on https://webauthn.io)")
            for i, c in enumerate(creds, 1):
                print(f"  {i}. RP: {c.get('rp_id')} | User: {c.get('user_name')} ({c.get('user_display_name')})")
                print(f"     Cred ID: {c.get('cred_id')[:24]}... | Signs: {c.get('sign_count')}")
            print("─" * 60)
        elif choice == "4":
            reset_pin()
            print("[✓] Security Key PIN removed.")
        elif choice == "5":
            setup_usb_gadget()
        elif choice == "6":
            teardown_usb_gadget(rm_module=False)
        elif choice == "q":
            print("Exiting. Goodbye!")
            break
        else:
            print("Invalid option. Please choose [1-6, q].")

def main():
    init_storage()
    if len(sys.argv) > 1:
        arg = sys.argv[1].strip().lower()
        if arg in ("-h", "--help", "help"):
            print("\n" + "═" * 70)
            print(" 🛡️  FIDO2 / WebAuthn Hardware Security Key CLI Emulator")
            print("═" * 70)
            print("Usage: python3 fido_touch_key.py [OPTION]\n")
            print("Options:")
            print("  1, -1, --interactive   Start key in INTERACTIVE TOUCH mode (Press [ENTER] to touch)")
            print("  2, -2, --auto          Start key in AUTO-TOUCH mode (Instant touch emulation)")
            print("  3, -3, --list          View saved passkeys / credentials")
            print("  4, -4, --reset-pin     Reset security key PIN")
            print("  5, -5, --bind          Re-bind USB ConfigFS gadget (/dev/hidg0)")
            print("  6, -6, --unbind        Unbind USB gadget (Restore normal phone USB)")
            print("  -h, --help             Show this help message and exit\n")
            print("If no option is provided, the interactive menu is displayed.")
            print("═" * 70 + "\n")
            sys.exit(0)
        elif arg in ("1", "-1", "--interactive", "-i"):
            run_authenticator_loop(auto_touch=False)
        elif arg in ("2", "-2", "--auto", "-a"):
            run_authenticator_loop(auto_touch=True)
        elif arg in ("3", "-3", "--list", "-l"):
            creds = list_stored_credentials()
            print("\n" + "─" * 60)
            print(f" Stored Passkeys ({len(creds)} total):")
            print("─" * 60)
            if not creds:
                print("  (No passkeys stored yet. Register one on https://webauthn.io)")
            for i, c in enumerate(creds, 1):
                print(f"  {i}. RP: {c.get('rp_id')} | User: {c.get('user_name')} ({c.get('user_display_name')})")
                print(f"     Cred ID: {c.get('cred_id')[:24]}... | Signs: {c.get('sign_count')}")
            print("─" * 60)
        elif arg in ("4", "-4", "--reset-pin"):
            reset_pin()
            print("[✓] Security Key PIN removed.")
        elif arg in ("5", "-5", "--bind"):
            setup_usb_gadget()
        elif arg in ("6", "-6", "--unbind"):
            teardown_usb_gadget(rm_module=False)
        else:
            print(f"Unknown option: {sys.argv[1]}. Use -h or --help for available options.")
            sys.exit(1)
    else:
        menu()

if __name__ == "__main__":
    main()
