"""Liveness reporting over the systemd ``sd_notify`` protocol.

A long-polling bot can wedge without dying: a half-open socket, a proxy that
went away mid-connection, or a deadlocked connector all leave a process that
looks healthy to any supervisor watching only for exit codes. Restart-on-exit
therefore cannot recover it.

The protocol here is the systemd one, so it works unchanged under systemd and
under the FreeBSD ``sdnotify-supervise`` helper the server runs. The bot pings
only after a probe actually reached Telegram, so the ping means "the whole
chain process → proxy → Telegram works", not merely "the process exists".
Miss enough pings and the supervisor kills and restarts the bot.

With no ``NOTIFY_SOCKET`` in the environment (running by hand, or a supervisor
that does not speak the protocol) every function here degrades to a no-op.
"""

import asyncio
import logging
import os
import socket

logger = logging.getLogger(__name__)


def sd_notify(state: str) -> bool:
    """Send a state line to the supervisor. Returns ``False`` when there is no
    socket to send to, which is the normal case outside a supervised run."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    # A leading '@' denotes systemd's abstract namespace, spelled as a NUL byte
    # in the kernel API. FreeBSD has no abstract sockets, so its supervisor
    # passes a filesystem path and this branch simply never fires there.
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(addr)
            sock.sendall(state.encode())
    except OSError as exc:
        logger.warning("sd_notify(%r) failed: %s", state, exc)
        return False
    return True


async def run_watchdog(bot: object, *, interval: float, probe_timeout: float) -> None:
    """Ping the supervisor every ``interval`` seconds, but only after a
    lightweight ``getMe`` confirms Telegram is actually reachable.

    A failed probe is deliberately met with silence rather than an exception:
    staying quiet is what eventually triggers the supervisor's restart, while
    raising here would only kill the task that is supposed to be reporting.
    """
    if not os.environ.get("NOTIFY_SOCKET"):
        logger.info("NOTIFY_SOCKET unset — watchdog disabled (not running supervised).")
        return

    logger.info("Watchdog active: probing every %gs with a %gs timeout.", interval, probe_timeout)
    while True:
        await asyncio.sleep(interval)
        try:
            # The outer timeout guards against a stall outside the HTTP request
            # itself, where request_timeout would never fire.
            async with asyncio.timeout(probe_timeout + 5):
                await bot.get_me(request_timeout=int(probe_timeout))  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — any failed probe means "unhealthy"
            logger.warning(
                "Telegram probe failed (%s) — withholding ping so the supervisor restarts us.",
                type(exc).__name__,
            )
        else:
            sd_notify("WATCHDOG=1")
