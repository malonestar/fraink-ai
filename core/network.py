import asyncio
import ipaddress
import logging
import re
import subprocess
from typing import Optional

from core import tailscale

log = logging.getLogger(__name__)


def is_home_network(device_ip: str, home_subnet: str) -> bool:
    try:
        return ipaddress.ip_address(device_ip) in ipaddress.ip_network(home_subnet)
    except ValueError:
        return False


def get_device_ip() -> Optional[str]:
    try:
        result = subprocess.run(
            ["hostname", "-I"], capture_output=True, text=True, timeout=5
        )
        ips = result.stdout.strip().split()
        for ip in ips:
            if not ip.startswith("100."):
                return ip
        return ips[0] if ips else None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def get_wifi_ssid() -> Optional[str]:
    for cmd in [["iwgetid", "-r"], ["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"]]:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if cmd[0] == "nmcli":
                for line in result.stdout.strip().split("\n"):
                    if line.startswith("yes:"):
                        ssid = line.split(":", 1)[1]
                        if ssid:
                            return ssid
            else:
                ssid = result.stdout.strip()
                if ssid:
                    return ssid
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
    return None


def _kf_escape(value: str) -> str:
    """GKeyFile string escaping (backslash first)."""
    return (value.replace("\\", "\\\\").replace("\n", "\\n")
            .replace("\t", "\\t").replace(";", "\\;"))


def build_keyfile(ssid: str, password: str) -> str:
    """NetworkManager keyfile for a client Wi-Fi profile (open if no password)."""
    lines = ["[connection]", f"id={_kf_escape(ssid)}", "type=wifi",
             "interface-name=wlan0", "autoconnect=true", "",
             "[wifi]", "mode=infrastructure", f"ssid={_kf_escape(ssid)}", ""]
    if password:
        lines += ["[wifi-security]", "key-mgmt=wpa-psk", f"psk={_kf_escape(password)}", ""]
    lines += ["[ipv4]", "method=auto", "", "[ipv6]", "method=auto", ""]
    return "\n".join(lines)


class NetworkManager:
    def __init__(self, config: dict):
        self._home_subnet = config.get("network", {}).get("home_subnet", "192.168.0.0/24")
        self._lan_url = config.get("server", {}).get("websocket_url_lan", "")
        self._ts_url = config.get("server", {}).get("websocket_url_tailscale", "")
        self._ap_ssid = config.get("network", {}).get("ap_ssid", "PiSugar-Setup")
        self._ap_timeout = config.get("network", {}).get("ap_timeout", 15)
        self.is_connected = False
        self.is_home = False
        self.ssid: Optional[str] = None
        self.device_ip: Optional[str] = None
        self._ap_active = False
        self._ts_up_task: Optional[asyncio.Task] = None
        self._scan_cache: list[dict] = []
        self._pending_ssid: Optional[str] = None

    def refresh(self) -> None:
        """Re-read SSID / IP / home-vs-roaming (cheap; blocking subprocess)."""
        self.ssid = get_wifi_ssid()
        self.device_ip = get_device_ip()
        self.is_connected = bool(self.ssid and self.device_ip)
        if self.device_ip:
            self.is_home = is_home_network(self.device_ip, self._home_subnet)

    def current_websocket_url(self) -> str:
        """Refresh, then pick LAN vs tailnet URL. Used as the xiaozhi
        client's URL source so it follows network changes."""
        self.refresh()
        return self.get_websocket_url()

    def get_websocket_url(self) -> str:
        return self._lan_url if self.is_home else self._ts_url

    async def wait_for_wifi(self) -> bool:
        for _ in range(self._ap_timeout):
            self.ssid = get_wifi_ssid()
            self.device_ip = get_device_ip()
            if self.ssid and self.device_ip:
                self.is_connected = True
                self.is_home = is_home_network(self.device_ip, self._home_subnet)
                if not self.is_home:
                    # Fire-and-forget: don't stall boot up to 30s on a first-run
                    # roaming auth. up() never raises; the result is unused here
                    # (the Tailscale app surfaces any auth URL). Keep a ref so the
                    # task isn't GC'd mid-flight.
                    self._ts_up_task = asyncio.create_task(tailscale.up())
                log.info("WiFi connected: %s (%s) — %s",
                         self.ssid, self.device_ip,
                         "home" if self.is_home else "roaming")
                return True
            await asyncio.sleep(1)
        log.warning("WiFi not connected after %ds", self._ap_timeout)
        return False

    # --- setup access point (NetworkManager) ---------------------------------
    # The Pi OS image runs NetworkManager, so the AP is an NM "shared" Wi-Fi
    # connection (NM starts its own dnsmasq for DHCP; the captive-portal DNS
    # override lives in /etc/NetworkManager/dnsmasq-shared.d/pisugar-portal.conf,
    # installed by setup). The Zero 2W has one radio: while the AP is up the
    # device is off its normal Wi-Fi, and it rejoins when the AP comes down.
    # (2026-11-09: replaced a hostapd + wpa_supplicant version that never
    # worked under NetworkManager.)

    AP_CON = "pisugar-ap"
    AP_IP = "192.168.4.1"

    @staticmethod
    def _nmcli(*args: str, timeout: int = 20) -> subprocess.CompletedProcess:
        return subprocess.run(["sudo", "nmcli", *args], capture_output=True,
                              text=True, timeout=timeout)

    async def start_ap(self) -> str:
        # Scan while still a client: scans are unreliable once in AP mode,
        # and the portal's network list is served from this cache.
        self._scan_cache = await asyncio.to_thread(self._scan_now)
        try:
            await asyncio.to_thread(self._nmcli, "connection", "delete", self.AP_CON)
            add = await asyncio.to_thread(
                self._nmcli, "connection", "add", "type", "wifi", "ifname", "wlan0",
                "con-name", self.AP_CON, "autoconnect", "no", "ssid", self._ap_ssid,
                "802-11-wireless.mode", "ap", "802-11-wireless.band", "bg",
                "802-11-wireless.channel", "6",
                "ipv4.method", "shared", "ipv4.addresses", f"{self.AP_IP}/24",
                "ipv6.method", "disabled")
            if add.returncode != 0:
                raise RuntimeError(add.stderr.strip() or add.stdout.strip())
            up = await asyncio.to_thread(self._nmcli, "connection", "up", self.AP_CON,
                                         timeout=40)
            if up.returncode != 0:
                raise RuntimeError(up.stderr.strip() or up.stdout.strip())
            self._ap_active = True
            log.info("AP started: %s at %s (%d networks cached)",
                     self._ap_ssid, self.AP_IP, len(self._scan_cache))
        except Exception as e:
            log.error("Failed to start AP: %s", e)
            await self.stop_ap(force=True)
        return self.AP_IP

    async def stop_ap(self, force: bool = False):
        if not (self._ap_active or force):
            return
        try:
            await asyncio.to_thread(self._nmcli, "connection", "down", self.AP_CON)
            await asyncio.to_thread(self._nmcli, "connection", "delete", self.AP_CON)
            self._ap_active = False
            log.info("AP stopped")
        except Exception as e:
            log.error("Failed to stop AP: %s", e)
        ssid, self._pending_ssid = self._pending_ssid, None
        await self.rejoin_wifi(ssid)

    async def rejoin_wifi(self, ssid: Optional[str] = None):
        """Bring the client connection back: the named one if given (just
        provisioned), else whatever saved network NetworkManager prefers."""
        try:
            if ssid:
                r = await asyncio.to_thread(self._nmcli, "connection", "up", "id", ssid,
                                            timeout=45)
                if r.returncode == 0:
                    return
                log.warning("Joining %s failed: %s", ssid, r.stderr.strip())
            await asyncio.to_thread(self._nmcli, "device", "connect", "wlan0", timeout=45)
        except Exception as e:
            log.error("Rejoin Wi-Fi failed: %s", e)

    @staticmethod
    def _scan_now() -> list[dict]:
        try:
            r = subprocess.run(
                ["sudo", "nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi",
                 "list", "--rescan", "yes"],
                capture_output=True, text=True, timeout=20)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []
        best: dict[str, dict] = {}
        for line in r.stdout.splitlines():
            parts = re.split(r"(?<!\\):", line)
            if len(parts) < 3 or not parts[0]:
                continue
            ssid = parts[0].replace("\\:", ":")
            try:
                signal = int(parts[1])
            except ValueError:
                signal = 0
            if ssid not in best or signal > best[ssid]["signal"]:
                best[ssid] = {"ssid": ssid, "signal": signal,
                              "secure": parts[2] not in ("", "--")}
        return sorted(best.values(), key=lambda n: -n["signal"])

    def scan_networks(self) -> list[dict]:
        if self._ap_active:
            return list(self._scan_cache)
        return self._scan_now()

    def save_wifi_credentials(self, ssid: str, password: str) -> bool:
        """Create/replace a NetworkManager client profile for ssid.

        Written as a keyfile through `sudo tee` (password on stdin) rather
        than `nmcli ... wifi-sec.psk <pw>`: sudo logs full command lines to
        the journal, which would leave the Wi-Fi password in plain text.
        """
        try:
            self._nmcli("connection", "delete", "id", ssid)
            path = ("/etc/NetworkManager/system-connections/"
                    f"pisugar-{re.sub(r'[^A-Za-z0-9_-]+', '_', ssid)[:40]}.nmconnection")
            r = subprocess.run(["sudo", "tee", path], input=build_keyfile(ssid, password),
                               capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                log.error("Saving Wi-Fi %s failed: %s", ssid, r.stderr.strip())
                return False
            subprocess.run(["sudo", "chmod", "600", path], timeout=5)
            r = self._nmcli("connection", "reload")
            if r.returncode != 0:
                log.error("Reloading Wi-Fi profiles failed: %s", r.stderr.strip())
                return False
            self._pending_ssid = ssid
            log.info("Saved Wi-Fi profile for %s", ssid)
            return True
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            log.error("Failed to save WiFi credentials: %s", e)
            return False
