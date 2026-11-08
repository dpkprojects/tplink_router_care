"""Thin wrapper around the (synchronous) tplinkrouterc6u library.

Every method in this module performs blocking network I/O. They must NEVER be
called from the Home Assistant event loop directly; always go through
``hass.async_add_executor_job(...)``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import logging
import secrets
import string
import threading
from urllib.parse import quote

import requests
from tplinkrouterc6u import (
    AbstractRouter,
    AuthorizeError,
    ClientException,
    Firmware,
    Status,
    TplinkRouterProvider,
)

from .const import (
    GUEST_2G5G_FORM,
    GUEST_5G_FORM,
    GUEST_PASSWORD_LENGTH,
    LOCK_TIMEOUT,
    REQUEST_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)


class RouterError(Exception):
    """Base error raised by this wrapper."""


class RouterConnectionError(RouterError):
    """Router could not be reached."""


class RouterAuthError(RouterError):
    """Router rejected the credentials."""


class RouterLoginError(RouterError):
    """Router refused the login without saying why.

    Can be a wrong password, or someone else holding the router's single admin
    session. The coordinator only treats it as a wrong password once it keeps
    happening.
    """


class RouterUnsupportedError(RouterError):
    """No client in the library could talk to this router."""


class RouterBusyError(RouterError):
    """Another operation (possibly a timed-out one) still holds the router."""


@dataclass(slots=True, frozen=True)
class GuestWifi:
    """State of one guest Wi-Fi band."""

    ssid: str | None
    enabled: bool
    password: str | None
    # Router's psk_version, e.g. "auto" (WPA2/WPA3) or "sae_only" (WPA3 only).
    psk_version: str | None = None
    hidden: bool = False


@dataclass(slots=True)
class RouterData:
    """Snapshot of router state from one poll."""

    firmware: Firmware | None
    status: Status
    # None if the router doesn't expose the guest_5g form (or reading it failed).
    guest_5g: GuestWifi | None = None
    # True if reading guest_5g failed on a network error (a glitch, not "this
    # router has no guest network"); the coordinator keeps the previous value.
    guest_5g_failed: bool = False
    # When this snapshot was read from the router (UTC); set by the coordinator.
    fetched_at: datetime | None = None


def generate_password(length: int = GUEST_PASSWORD_LENGTH) -> str:
    """Random letters + digits, with at least one of each.

    Letters and digits only: easy to type and nothing needs escaping in the
    router request or a Wi-Fi QR code.
    """
    alphabet = string.ascii_letters + string.digits
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(c.isalpha() for c in password) and any(c.isdigit() for c in password):
            return password


def normalize_host(host: str) -> str:
    """Strip whitespace and trailing slashes; keep an explicit scheme if given."""
    return host.strip().rstrip("/")


class RouterApi:
    """Serialises access to one router.

    TP-Link routers typically allow a single admin session, so every operation
    logs in, does its work and logs out again, and a lock guarantees two
    operations (e.g. a poll and a reboot button press) never overlap.
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        client_class: str | None = None,
    ) -> None:
        self._host = normalize_host(host)
        self._username = username
        self._password = password
        self._client_class = client_class
        self._lock = threading.Lock()
        self._firmware: Firmware | None = None

    @property
    def client_class(self) -> str | None:
        """Name of the library client class that works with this router."""
        return self._client_class

    # ------------------------------------------------------------------ #
    # Blocking helpers (run in executor only)
    # ------------------------------------------------------------------ #

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Hold the router lock, but never wait for it indefinitely."""
        if not self._lock.acquire(timeout=LOCK_TIMEOUT):
            raise RouterBusyError(
                "Router is still busy with a previous request; will retry"
            )
        try:
            yield
        finally:
            self._lock.release()

    def _new_client(self) -> AbstractRouter:
        clients = TplinkRouterProvider.get_clients()
        cls = clients.get(self._client_class or "")
        if cls is None:
            raise RouterUnsupportedError(
                f"Unknown router client class: {self._client_class}"
            )
        # verify_ssl=False: routers serving HTTPS use self-signed certificates.
        return cls(
            self._host, self._password, self._username, _LOGGER, False, REQUEST_TIMEOUT
        )

    def detect(self) -> Firmware | None:
        """Auto-detect the router model, log in once and return its firmware info.

        Slow: the library may probe many client implementations in turn.
        """
        with self._locked():
            try:
                router = TplinkRouterProvider.get_client(
                    self._host,
                    self._password,
                    self._username,
                    _LOGGER,
                    False,
                    REQUEST_TIMEOUT,
                )
            except requests.exceptions.RequestException as err:
                raise RouterConnectionError(str(err)) from err
            except ClientException as err:
                # Library raises this both for a wrong password and for an
                # unsupported model; it can't tell the two apart.
                raise RouterUnsupportedError(str(err)) from err

            self._client_class = type(router).__name__
            return self._run_session(router, fetch_status=False).firmware

    def fetch(self) -> RouterData:
        """Log in, read firmware + status, log out."""
        with self._locked():
            return self._run_session(self._new_client(), fetch_status=True)

    def reboot(self) -> None:
        """Log in and reboot the router."""
        with self._locked():
            router = self._new_client()
            self._authorize(router)
            try:
                router.reboot()
            except requests.exceptions.RequestException:
                # The router often drops the connection while rebooting.
                _LOGGER.debug("Connection dropped during reboot (expected)")
            except ClientException as err:
                raise RouterError(f"Reboot failed: {err}") from err
            # No logout: the router is going down.

    def set_guest_5g_enabled(self, enable: bool) -> GuestWifi:
        """Turn the 5 GHz guest network on or off; returns the new state."""
        return self._write_guest_5g(
            [(GUEST_5G_FORM, f"operation=write&enable={'on' if enable else 'off'}")],
            lambda guest: guest.enabled == enable,
        )

    def set_guest_5g_password(self, password: str) -> GuestWifi:
        """Set the 5 GHz guest network password; returns the new state.

        Routers such as the Archer AXE75 keep one shared key for 2.4 + 5 GHz
        guest in the guest_2g5g form; that's the key the radio uses and the web
        UI shows. guest_5g can echo back a value written to it while the shared
        key stays unchanged, so: write guest_2g5g first (falling back to
        guest_5g on routers without it) and only succeed once guest_5g AND the
        shared form (if present) both read back the new key. On shared-key
        routers this necessarily changes the 2.4 GHz guest password too.
        """
        if not 8 <= len(password) <= 63:
            raise ValueError("Wi-Fi password must be 8-63 characters")
        data = f"operation=write&psk_key={quote(password, safe='')}"
        return self._write_guest_5g(
            [(GUEST_2G5G_FORM, data), (GUEST_5G_FORM, data)],
            lambda guest: guest.password == password,
            shared_applied=lambda info: info.get("psk_key") == password,
        )

    # ------------------------------------------------------------------ #

    def _authorize(self, router: AbstractRouter) -> None:
        try:
            router.authorize()
        except AuthorizeError as err:
            raise RouterAuthError(str(err)) from err
        except requests.exceptions.RequestException as err:
            raise RouterConnectionError(str(err)) from err
        except ClientException as err:
            # Network failures were caught above, so the router answered and
            # refused us: usually a wrong password or a busy admin session.
            raise RouterLoginError(str(err)) from err

    @classmethod
    def _read_guest_5g(
        cls, router: AbstractRouter, *, active_key: bool = True
    ) -> GuestWifi | None:
        """Read the 5 GHz guest network from the router. Raises on request errors.

        With ``active_key`` (the default), the password is taken from the
        shared guest_2g5g form when the router has one, because that's the key
        the radio actually uses (guest_5g can hold a stale or echoed value).
        ``active_key=False`` reports guest_5g's own psk_key, for verification.
        """
        request = getattr(router, "request", None)
        if request is None:
            # Client class without the LuCI request API (not a router that has
            # this form).
            return None
        info = request(f"admin/wireless?form={GUEST_5G_FORM}", "operation=read")
        if not isinstance(info, dict):
            return None
        password = info.get("psk_key")
        if active_key:
            shared = cls._read_shared_guest(router)
            if shared is not None and shared.get("psk_key"):
                password = shared["psk_key"]
        return GuestWifi(
            ssid=info.get("ssid"),
            enabled=info.get("enable") == "on",
            password=password,
            psk_version=info.get("psk_version"),
            hidden=info.get("hidden") == "on",
        )

    @staticmethod
    def _read_shared_guest(router: AbstractRouter) -> dict | None:
        """The shared guest_2g5g form, or None if this router doesn't have it."""
        try:
            info = router.request(
                f"admin/wireless?form={GUEST_2G5G_FORM}", "operation=read"
            )
        except requests.exceptions.RequestException as err:
            raise RouterConnectionError(str(err)) from err
        except Exception:  # noqa: BLE001 - form doesn't exist on this model
            return None
        return info if isinstance(info, dict) and info else None

    def _write_guest_5g(
        self,
        attempts: list[tuple[str, str]],
        applied: Callable[[GuestWifi], bool],
        shared_applied: Callable[[dict], bool] | None = None,
    ) -> GuestWifi:
        """Try each (form, data) write in turn until the change reads back as applied.

        guest_5g must satisfy ``applied``; if ``shared_applied`` is given and
        the router has a shared guest_2g5g form, that form must satisfy it too.
        """
        with self._locked():
            router = self._new_client()
            if getattr(router, "request", None) is None:
                raise RouterUnsupportedError(
                    "This router model does not support guest Wi-Fi control"
                )
            self._authorize(router)
            problems: list[str] = []
            try:
                has_shared = (
                    shared_applied is not None
                    and self._read_shared_guest(router) is not None
                )
                for form, data in attempts:
                    try:
                        response = router.request(f"admin/wireless?form={form}", data)
                        _LOGGER.debug("Write to %s returned %s", form, response)
                    except requests.exceptions.RequestException as err:
                        raise RouterConnectionError(str(err)) from err
                    except Exception as err:  # noqa: BLE001 - library raises bare Exception
                        _LOGGER.debug("Write to %s failed", form, exc_info=True)
                        problems.append(f"{form}: {err}")
                        continue

                    try:
                        guest = self._read_guest_5g(router, active_key=False)
                    except requests.exceptions.RequestException as err:
                        raise RouterConnectionError(str(err)) from err
                    except Exception as err:  # noqa: BLE001
                        raise RouterError(f"Could not read back guest Wi-Fi: {err}") from err
                    _LOGGER.debug("guest_5g after writing %s: %s", form, guest)
                    if guest is None or not applied(guest):
                        problems.append(f"{form}: guest_5g not updated")
                        continue
                    if has_shared and shared_applied is not None:
                        shared = self._read_shared_guest(router)
                        _LOGGER.debug("guest_2g5g after writing %s: %s", form, shared)
                        if shared is None or not shared_applied(shared):
                            problems.append(f"{form}: shared guest_2g5g key not updated")
                            continue
                    # Confirmed. Report what the router now holds, from one
                    # final fresh read (not the value we sent).
                    try:
                        final = self._read_guest_5g(router)
                    except requests.exceptions.RequestException as err:
                        raise RouterConnectionError(str(err)) from err
                    except Exception as err:  # noqa: BLE001
                        raise RouterError(f"Could not read back guest Wi-Fi: {err}") from err
                    if final is None:
                        raise RouterError("Router stopped returning guest Wi-Fi settings")
                    return final

                raise RouterError(
                    "Router did not apply the guest Wi-Fi change ("
                    + "; ".join(problems)
                    + ")"
                )
            finally:
                try:
                    router.logout()
                except Exception:  # noqa: BLE001
                    _LOGGER.debug("Logout failed", exc_info=True)

    def _run_session(self, router: AbstractRouter, *, fetch_status: bool) -> RouterData:
        self._authorize(router)
        try:
            if self._firmware is None:
                try:
                    self._firmware = router.get_firmware()
                except Exception:  # noqa: BLE001 - firmware is optional info
                    _LOGGER.debug("Could not read firmware info", exc_info=True)
            status = router.get_status() if fetch_status else Status()
            guest_5g = None
            guest_5g_failed = False
            if fetch_status:
                try:
                    guest_5g = self._read_guest_5g(router)
                except (requests.exceptions.RequestException, RouterConnectionError):
                    guest_5g_failed = True
                    _LOGGER.debug("Network error reading guest 5 GHz Wi-Fi", exc_info=True)
                except Exception:  # noqa: BLE001 - guest info is optional
                    _LOGGER.debug("Could not read guest 5 GHz Wi-Fi", exc_info=True)
            return RouterData(
                firmware=self._firmware,
                status=status,
                guest_5g=guest_5g,
                guest_5g_failed=guest_5g_failed,
            )
        except requests.exceptions.RequestException as err:
            raise RouterConnectionError(str(err)) from err
        except ClientException as err:
            raise RouterError(str(err)) from err
        finally:
            try:
                router.logout()
            except Exception:  # noqa: BLE001 - best effort, never fail a poll on logout
                _LOGGER.debug("Logout failed", exc_info=True)
