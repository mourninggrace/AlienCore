"""
AlienCore - dns_bench.py
DNS speed test and setter.
Benchmarks public DNS providers by latency and applies the winner via netsh.
"""

import socket
import struct
import time
import subprocess
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger("aliencore.dns_bench")

# (Display name, primary IPv4, secondary IPv4, primary IPv6, secondary IPv6)
DNS_SERVERS = [
    ("Cloudflare",    "1.1.1.1",         "1.0.0.1",          "2606:4700:4700::1111", "2606:4700:4700::1001"),
    ("Google",        "8.8.8.8",         "8.8.4.4",           "2001:4860:4860::8888", "2001:4860:4860::8844"),
    ("Quad9",         "9.9.9.9",         "149.112.112.112",   "2620:fe::fe",          "2620:fe::9"),
    ("OpenDNS",       "208.67.222.222",  "208.67.220.220",    "2620:119:35::35",      "2620:119:53::53"),
    ("AdGuard",       "94.140.14.14",    "94.140.15.15",      "2a10:50c0::ad1:ff",   "2a10:50c0::ad2:ff"),
    ("NextDNS",       "45.90.28.0",      "45.90.30.0",        "2a07:a8c0::",          "2a07:a8c1::"),
    ("CleanBrowsing", "185.228.168.9",   "185.228.169.9",     "2a0d:2a00:1::",        "2a0d:2a00:2::"),
    ("Alternate DNS", "76.76.19.19",     "76.223.122.150",    "2602:fcbc::ad",        "2602:fcbc:2::ad"),
    ("ControlD",      "76.76.2.0",       "76.76.10.0",        "2606:1a40::",          "2606:1a40:1::"),
]

_DNS_QUERY = (
    struct.pack(">HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0)
    + b"\ncloudflare\x03com\x00\x00\x01\x00\x01"
)


def ping_dns(server: str, timeout: float = 2.0):
    """
    Send a DNS A query for cloudflare.com to *server* and return
    round-trip time in milliseconds, or None on failure.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        t0 = time.perf_counter()
        s.sendto(_DNS_QUERY, (server, 53))
        s.recv(512)
        return (time.perf_counter() - t0) * 1000.0
    except Exception:
        return None
    finally:
        try:
            s.close()
        except Exception:
            pass


def benchmark(count: int = 4, timeout: float = 2.0) -> list:
    """
    Benchmark all DNS_SERVERS concurrently (one thread per provider).
    Returns a list of dicts sorted fastest-first:
      {name, primary, secondary, latency_ms, failed}
    """
    def _test(name, primary, secondary, primary_v6, secondary_v6):
        samples = []
        for _ in range(count):
            t = ping_dns(primary, timeout)
            if t is not None:
                samples.append(t)
        if samples:
            samples.sort()
            median = samples[len(samples) // 2]
            return {"name": name, "primary": primary, "secondary": secondary,
                    "primary_v6": primary_v6, "secondary_v6": secondary_v6,
                    "latency_ms": median, "failed": False}
        return {"name": name, "primary": primary, "secondary": secondary,
                "primary_v6": primary_v6, "secondary_v6": secondary_v6,
                "latency_ms": None, "failed": True}

    results = []
    with ThreadPoolExecutor(max_workers=len(DNS_SERVERS)) as ex:
        futures = [ex.submit(_test, n, p, s, p6, s6) for n, p, s, p6, s6 in DNS_SERVERS]
        for f in as_completed(futures):
            try:
                results.append(f.result())
            except Exception:
                pass

    results.sort(key=lambda r: (r["failed"], r["latency_ms"] or 9999))
    return results


def get_active_interfaces() -> list:
    """
    Return a list of connected interface names (netsh-compatible).
    Parses output of 'netsh interface show interface'.
    """
    try:
        out = subprocess.check_output(
            ["netsh", "interface", "show", "interface"],
            text=True, stderr=subprocess.DEVNULL, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        names = []
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[0] == "Enabled" and parts[1] == "Connected":
                # Interface name is everything after the first 3 columns
                name = " ".join(parts[3:])
                names.append(name)
        return names
    except Exception:
        return []


def set_dns(interface: str, primary: str, secondary: str = None,
            primary_v6: str = None, secondary_v6: str = None) -> tuple:
    """
    Set static IPv4 and IPv6 DNS on *interface*.
    Returns (success: bool, message: str). Flushes DNS cache on success.
    """
    try:
        # ── IPv4 ─────────────────────────────────────────────────────────────
        _cnw = subprocess.CREATE_NO_WINDOW
        r = subprocess.run(
            ["netsh", "interface", "ip", "set", "dns",
             f"name={interface}", "source=static", f"addr={primary}", "register=primary"],
            capture_output=True, text=True, timeout=10, creationflags=_cnw
        )
        if r.returncode != 0:
            msg = (r.stderr or r.stdout).strip() or "netsh returned non-zero"
            return False, msg

        if secondary:
            subprocess.run(
                ["netsh", "interface", "ip", "add", "dns",
                 f"name={interface}", f"addr={secondary}", "index=2"],
                capture_output=True, timeout=10, creationflags=_cnw
            )

        # ── IPv6 ─────────────────────────────────────────────────────────────
        if primary_v6:
            subprocess.run(
                ["netsh", "interface", "ipv6", "set", "dns",
                 f"name={interface}", "source=static", f"addr={primary_v6}", "register=primary"],
                capture_output=True, timeout=10, creationflags=_cnw
            )
            if secondary_v6:
                subprocess.run(
                    ["netsh", "interface", "ipv6", "add", "dns",
                     f"name={interface}", f"addr={secondary_v6}", "index=2"],
                    capture_output=True, timeout=10, creationflags=_cnw
                )

        subprocess.run(["ipconfig", "/flushdns"], capture_output=True, timeout=10, creationflags=_cnw)
        return True, f"DNS set to {primary} / {primary_v6 or 'no IPv6'}"
    except Exception as e:
        return False, str(e)


def reset_to_dhcp(interface: str) -> tuple:
    """
    Restore IPv4 and IPv6 DNS to automatic (DHCP) on *interface*.
    Returns (success: bool, message: str).
    """
    try:
        # ── IPv4 ─────────────────────────────────────────────────────────────
        _cnw = subprocess.CREATE_NO_WINDOW
        r = subprocess.run(
            ["netsh", "interface", "ip", "set", "dns",
             f"name={interface}", "source=dhcp"],
            capture_output=True, text=True, timeout=10, creationflags=_cnw
        )
        if r.returncode != 0:
            msg = (r.stderr or r.stdout).strip() or "netsh returned non-zero"
            return False, msg

        # ── IPv6 ─────────────────────────────────────────────────────────────
        subprocess.run(
            ["netsh", "interface", "ipv6", "set", "dns",
             f"name={interface}", "source=dhcp"],
            capture_output=True, timeout=10, creationflags=_cnw
        )

        subprocess.run(["ipconfig", "/flushdns"], capture_output=True, timeout=10, creationflags=_cnw)
        return True, "DNS reset to DHCP (automatic) — IPv4 and IPv6"
    except Exception as e:
        return False, str(e)
