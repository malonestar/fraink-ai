from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.display import Display
    from core.button import ButtonListener
    from core.network import NetworkManager
    from core.xiaozhi_client import XiaozhiClient
    from core.audio import AudioService
    from core.wakeword import WakeWordService
    from core.settings_store import SettingsStore


@dataclass
class AppContext:
    display: "Display" = field(default=None)
    button: "ButtonListener" = field(default=None)
    network: "NetworkManager" = field(default=None)
    xiaozhi: "XiaozhiClient" = field(default=None)
    audio: "AudioService" = field(default=None)
    wakeword: "WakeWordService" = field(default=None)
    config: dict = field(default_factory=dict)
    battery_pct: int = -1
    battery_plugged: bool = False
    tailscale_state: str = "NotInstalled"
    settings: "SettingsStore" = field(default=None)
