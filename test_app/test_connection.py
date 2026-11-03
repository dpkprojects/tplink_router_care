#!/usr/bin/env python3
"""Standalone connectivity tester for TP-Link routers (no Home Assistant needed).

Uses the same tplinkrouterc6u library as the integration and the same pattern:
blocking library calls run in a worker thread with a hard timeout, so the
asyncio loop stays responsive (a heartbeat prints while we wait).

Usage:
    python test_connection.py 192.168.0.1
    python test_connection.py 192.168.0.1 --username admin --debug
Password is prompted (or read from the TPLINK_PASSWORD env var).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import os
import sys
import time
from urllib.parse import urlsplit

from tplinkrouterc6u import AuthorizeError, ClientException, TplinkRouterProvider

REQUEST_TIMEOUT = 10
DETECT_TIMEOUT = 180


def step(ok: bool, msg: str) -> None:
    print(f"  [{'OK' if ok else 'FAIL'}] {msg}", flush=True)


async def tcp_check(host: str) -> bool:
    parts = urlsplit(host if "://" in host else f"http://{host}")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        async with asyncio.timeout(5):
            _, writer = await asyncio.open_connection(parts.hostname, port)
    except (OSError, TimeoutError) as err:
        step(False, f"TCP {parts.hostname}:{port} unreachable ({str(err) or 'timeout'})")
        return False
    writer.close()
    await writer.wait_closed()
    step(True, f"TCP {parts.hostname}:{port} reachable")
    return True


def blocking_session(host: str, username: str, password: str, logger) -> dict:
    """Everything that talks HTTP to the router. Runs in a thread."""
    t0 = time.monotonic()
    router = TplinkRouterProvider.get_client(
        host, password, username, logger, False, REQUEST_TIMEOUT
    )
    detected = time.monotonic() - t0
    router.authorize()
    try:
        firmware = router.get_firmware()
        status = router.get_status()
    finally:
        try:
            router.logout()
        except Exception:  # noqa: BLE001
            pass
    return {
        "client": type(router).__name__,
        "detect_seconds": detected,
        "firmware": firmware,
        "status": status,
    }


async def heartbeat() -> None:
    start = time.monotonic()
    while True:
        await asyncio.sleep(5)
        print(
            f"  ... still working ({time.monotonic() - start:.0f}s, event loop responsive)",
            flush=True,
        )


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("host", help="Router IP, e.g. 192.168.0.1 (prefix https:// if needed)")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--debug", action="store_true", help="Show library debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger("tplink_test") if args.debug else None

    host = args.host.strip().rstrip("/")
    password = os.environ.get("TPLINK_PASSWORD") or getpass.getpass("Router password: ")

    print(f"\nTesting {host} as '{args.username}'")
    if not await tcp_check(host):
        return 1

    print("  Detecting router model and logging in (can take a while)...")
    hb = asyncio.create_task(heartbeat())
    try:
        async with asyncio.timeout(DETECT_TIMEOUT):
            result = await asyncio.to_thread(
                blocking_session, host, args.username, password, logger
            )
    except TimeoutError:
        step(False, f"No answer within {DETECT_TIMEOUT}s")
        return 1
    except AuthorizeError as err:
        step(False, f"Authentication rejected: {err}")
        return 1
    except ClientException as err:
        step(False, f"Login/detection failed: {err}")
        return 1
    except Exception as err:  # noqa: BLE001
        step(False, f"{type(err).__name__}: {err}")
        return 1
    finally:
        hb.cancel()

    fw, st = result["firmware"], result["status"]
    step(True, f"Client class: {result['client']} (detected in {result['detect_seconds']:.1f}s)")
    step(True, f"Model: {fw.model}  HW: {fw.hardware_version}  FW: {fw.firmware_version}")
    print("\n  Status")
    rows = {
        "WAN IP": st.wan_ipv4_addr,
        "LAN IP": st.lan_ipv4_addr,
        "WAN uptime (s)": st.wan_ipv4_uptime,
        "Connection type": st.conn_type,
        "Clients total": st.clients_total,
        "Wi-Fi clients": st.wifi_clients_total,
        "Wired clients": st.wired_total,
        "Guest clients": st.guest_clients_total,
        "CPU usage": f"{st.cpu_usage * 100:.0f}%" if st.cpu_usage is not None else None,
        "Memory usage": f"{st.mem_usage * 100:.0f}%" if st.mem_usage is not None else None,
    }
    for k, v in rows.items():
        print(f"    {k:<16} {v if v is not None else '-'}")
    if st.devices:
        print(f"\n  Devices ({len(st.devices)})")
        for d in st.devices:
            print(
                f"    {d.type.value:<10} {d.ipaddr or '-':<16} {d.macaddr or '-':<18} {d.hostname or ''}"
            )
    print(
        "\nConnectivity test passed. The Home Assistant integration should work with these settings."
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
