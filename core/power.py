import logging
import subprocess

log = logging.getLogger(__name__)

SERVICE_NAME = "fraink"


def _run(cmd: list[str]):
    log.info("power action: %s", " ".join(cmd))
    try:
        subprocess.Popen(cmd)
    except Exception as e:
        log.error("power action failed (%s): %s", " ".join(cmd), e)


def restart_app():
    _run(["sudo", "systemctl", "restart", SERVICE_NAME])


def reboot():
    _run(["sudo", "reboot"])


def poweroff():
    _run(["sudo", "poweroff"])
