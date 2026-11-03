"""Thin wrapper around the (synchronous) tplinkrouterc6u library.

Every method in this module performs blocking network I/O. They must NEVER be
called from the Home Assistant event loop directly; always go through
``hass.async_add_executor_job(...)``.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import threading

import requests
from tplinkrouterc6u import (
    AbstractRouter,
    AuthorizeError,
    ClientException,
    Firmware,
    Status,
    TplinkRouterProvider,
)

from .const import REQUEST_TIMEOUT

_LOGGER = logging.getLogger(__name__)


class RouterError(Exception):
    """Base error raised by this wrapper."""


class RouterConnectionError(RouterError):
    """Router could not be reached."""


class RouterAuthError(RouterError):
    """Router rejected the credentials."""


class RouterUnsupportedError(RouterError):
    """No client in the library could talk to this router."""


@dataclass(slots=True)
class RouterData:
    """Snapshot of router state from one poll."""

    firmware: Firmware | None
    status: Status


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
        with self._lock:
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
        with self._lock:
            return self._run_session(self._new_client(), fetch_status=True)

    def reboot(self) -> None:
        """Log in and reboot the router."""
        with self._lock:
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

    # ------------------------------------------------------------------ #

    def _authorize(self, router: AbstractRouter) -> None:
        try:
            router.authorize()
        except AuthorizeError as err:
            raise RouterAuthError(str(err)) from err
        except requests.exceptions.RequestException as err:
            raise RouterConnectionError(str(err)) from err
        except ClientException as err:
            raise RouterError(str(err)) from err

    def _run_session(self, router: AbstractRouter, *, fetch_status: bool) -> RouterData:
        self._authorize(router)
        try:
            if self._firmware is None:
                try:
                    self._firmware = router.get_firmware()
                except Exception:  # noqa: BLE001 - firmware is optional info
                    _LOGGER.debug("Could not read firmware info", exc_info=True)
            status = router.get_status() if fetch_status else Status()
            return RouterData(firmware=self._firmware, status=status)
        except requests.exceptions.RequestException as err:
            raise RouterConnectionError(str(err)) from err
        except ClientException as err:
            raise RouterError(str(err)) from err
        finally:
            try:
                router.logout()
            except Exception:  # noqa: BLE001 - best effort, never fail a poll on logout
                _LOGGER.debug("Logout failed", exc_info=True)
