#!/usr/bin/env python3
"""Set the 5 GHz guest Wi-Fi password on a TP-Link router (no Home Assistant needed).

Same logic as the integration's "New guest 5 GHz password" button:
  1. GET:  read the password currently set on the router;
  2. SET:  write the new key to the shared guest_2g5g form first (routers like
           the Archer AXE75 keep one key for 2.4 + 5 GHz guest, and that's the
           one the web UI shows and the radio uses); fall back to guest_5g;
  3. VERIFY: read guest_5g AND guest_2g5g back from the router; both must match.

Note: on shared-key routers guest_5g can echo back a value written to it even
though the active key (guest_2g5g) is unchanged, so checking guest_5g alone
is not enough.

Usage:
    python set_guest_password.py 192.168.0.1                 # random 8-char password
    python set_guest_password.py 192.168.0.1 MyNewPass123    # specific password
    python set_guest_password.py 192.168.0.1 --client TplinkRouter --debug
Admin password is prompted (or read from the TPLINK_PASSWORD env var).
"""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import secrets
import string
import sys
from urllib.parse import quote

from tplinkrouterc6u import AuthorizeError, ClientException, TplinkRouterProvider

REQUEST_TIMEOUT = 10
PASSWORD_LENGTH = 8
GUEST_5G_FORM = "guest_5g"
GUEST_2G5G_FORM = "guest_2g5g"  # shared 2.4 + 5 GHz key on some models
GUEST_2G_FORM = "guest_2g"


def step(ok: bool, msg: str) -> None:
    print(f"  [{'OK' if ok else 'FAIL'}] {msg}", flush=True)


def generate_password(length: int = PASSWORD_LENGTH) -> str:
    """Random letters + digits, with at least one of each."""
    alphabet = string.ascii_letters + string.digits
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(c.isalpha() for c in password) and any(c.isdigit() for c in password):
            return password


def read_form(router, form: str) -> dict:
    info = router.request(f"admin/wireless?form={form}", "operation=read")
    return info if isinstance(info, dict) else {}


def write_key(router, form: str, key: str) -> None:
    router.request(
        f"admin/wireless?form={form}", f"operation=write&psk_key={quote(key, safe='')}"
    )


def connect(host: str, username: str, admin_password: str, client: str | None, logger):
    if client:
        cls = TplinkRouterProvider.get_clients().get(client)
        if cls is None:
            sys.exit(
                f"Unknown client class {client!r}. Known: "
                + ", ".join(TplinkRouterProvider.get_clients())
            )
        return cls(host, admin_password, username, logger, False, REQUEST_TIMEOUT)
    print("  Detecting router model (can take a while; use --client to skip)...")
    return TplinkRouterProvider.get_client(
        host, admin_password, username, logger, False, REQUEST_TIMEOUT
    )


def read_shared_key(router) -> str | None:
    """psk_key of the shared guest_2g5g form, or None if the router has no such form."""
    try:
        return read_form(router, GUEST_2G5G_FORM).get("psk_key")
    except Exception:  # noqa: BLE001 - form doesn't exist on this model
        return None


def keys_applied(router, password: str, has_shared: bool) -> bool:
    if read_form(router, GUEST_5G_FORM).get("psk_key") != password:
        return False
    return not has_shared or read_shared_key(router) == password


def set_guest_5g_password(router, password: str, has_shared: bool) -> bool:
    """Write guest_2g5g, then guest_5g, until every guest key reads back as new."""
    forms = (GUEST_2G5G_FORM, GUEST_5G_FORM)
    for i, form in enumerate(forms):
        last = i == len(forms) - 1
        tag = "  [FAIL]" if last else "  [SKIP]"
        try:
            write_key(router, form, password)
            print(f"  [..] Wrote key to {form}", flush=True)
        except Exception as err:  # noqa: BLE001 - library raises bare Exception
            print(f"{tag} Write to {form} rejected: {err}", flush=True)
            continue
        if keys_applied(router, password, has_shared):
            step(True, f"Password applied via {form}")
            return True
        print(f"{tag} After writing {form} the router still reports an old key", flush=True)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("host", help="Router IP, e.g. 192.168.0.1 (prefix https:// if needed)")
    parser.add_argument(
        "password", nargs="?", help="New guest password (default: random 8 characters)"
    )
    parser.add_argument("--username", default="admin")
    parser.add_argument(
        "--client",
        help="Library client class to use, e.g. TplinkRouter (skips slow model detection)",
    )
    parser.add_argument("--debug", action="store_true", help="Show library debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger("tplink_guest") if args.debug else None

    new_password = args.password or generate_password()
    if not 8 <= len(new_password) <= 63:
        sys.exit("Wi-Fi password must be 8-63 characters.")

    host = args.host.strip().rstrip("/")
    admin_password = os.environ.get("TPLINK_PASSWORD") or getpass.getpass(
        "Router admin password: "
    )

    print(f"\nConnecting to {host} as '{args.username}'")
    try:
        router = connect(host, args.username, admin_password, args.client, logger)
        router.authorize()
    except AuthorizeError as err:
        step(False, f"Authentication rejected: {err}")
        return 1
    except ClientException as err:
        step(False, f"Login/detection failed: {err}")
        return 1
    except Exception as err:  # noqa: BLE001
        step(False, f"{type(err).__name__}: {err}")
        return 1
    step(True, f"Logged in (client class: {type(router).__name__})")

    if getattr(router, "request", None) is None:
        step(False, f"{type(router).__name__} has no request API; guest Wi-Fi not supported")
        return 1

    try:
        # 1. GET: what the router has now
        print("\n1. Current guest 5 GHz settings (from router)")
        before = read_form(router, GUEST_5G_FORM)
        if not before:
            step(False, "Router did not return the guest_5g form")
            return 1
        shared_before = read_shared_key(router)
        has_shared = shared_before is not None
        print(f"    SSID      {before.get('ssid')}")
        print(f"    Enabled   {before.get('enable')}")
        print(f"    Password  {before.get('psk_key')}  (guest_5g form)")
        if has_shared:
            print(f"    Password  {shared_before}  (shared guest_2g5g form, used by web UI)")
        try:
            before_2g = read_form(router, GUEST_2G_FORM).get("psk_key")
        except Exception:  # noqa: BLE001 - informational only
            before_2g = None

        # 2. SET
        print(f"\n2. Setting new password: {new_password}")
        if keys_applied(router, new_password, has_shared):
            step(True, "Router already has this password; nothing to change")
        elif not set_guest_5g_password(router, new_password, has_shared):
            print("\nPassword was NOT changed. Re-run with --debug to see router responses.")
            return 1

        # 3. VERIFY: fresh read from the router
        print("\n3. Password now on the router (read back)")
        after = read_form(router, GUEST_5G_FORM)
        current = after.get("psk_key")
        shared_after = read_shared_key(router) if has_shared else None
        print(f"    SSID      {after.get('ssid')}")
        print(f"    Password  {current}  (guest_5g form)")
        if has_shared:
            print(f"    Password  {shared_after}  (shared guest_2g5g form, used by web UI)")
        if current != new_password or (has_shared and shared_after != new_password):
            step(False, f"Router still reports an old key, expected {new_password!r}")
            return 1
        step(True, "Router confirms the new password")
        try:
            after_2g = read_form(router, GUEST_2G_FORM).get("psk_key")
        except Exception:  # noqa: BLE001
            after_2g = None
        if before_2g is not None and after_2g is not None and before_2g != after_2g:
            print("  [NOTE] 2.4 GHz guest password changed too (shared key on this router)")
    except Exception as err:  # noqa: BLE001
        step(False, f"{type(err).__name__}: {err}")
        return 1
    finally:
        try:
            router.logout()
        except Exception:  # noqa: BLE001
            pass

    print(f"\nGuest 5 GHz password (confirmed by router): {current}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
