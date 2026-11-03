# TP-Link Router Care

A Home Assistant custom integration for TP-Link routers, built on
[`tplinkrouterc6u`](https://github.com/AlexandrErohin/TP-Link-Archer-C6U).
It also comes with a standalone script for testing the connection.

## 1. Test connectivity first (no Home Assistant needed)

```bash
python3 -m venv .venv
.venv/bin/pip install -r test_app/requirements.txt
.venv/bin/python test_app/test_connection.py 192.168.0.1          # prompts for password
.venv/bin/python test_app/test_connection.py 192.168.0.1 --debug  # verbose library logs
```

The script checks that the router's port is reachable, detects the model, logs in, and prints
the firmware, status and connected devices. If it passes, the integration will work with the
same IP and password.

> Log out of the router's web UI first. Most TP-Link routers allow only one admin session.

## 2. Install the integration

### With HACS (recommended)

1. In Home Assistant, open **HACS**, click the **⋮** menu (top right) and choose **Custom
   repositories**.
2. Add `https://github.com/dpkprojects/tplink_router_care` with type **Integration**.
3. Search HACS for **TP-Link Router Care**, open it and click **Download**.
4. Restart Home Assistant.

HACS will then offer updates whenever a new release is published.

### Manually

Copy `custom_components/tplink_router_care` into your Home Assistant `config/custom_components/`
folder and restart Home Assistant.

### Set it up

Go to **Settings → Devices & Services → Add Integration →
TP-Link Router Care** and enter:

| Field | Notes |
|---|---|
| Router IP address | e.g. `192.168.0.1`. Use `https://192.168.0.1` if the router only serves HTTPS. |
| Router password | The web UI password. |
| Username | Optional, defaults to `admin`. |

The first setup can take up to a couple of minutes, because the library tries each router model
in turn. The model it finds is saved, so later polls skip this step.

## Entities

- **Router reachable** (connectivity binary sensor). Turns off when a poll fails.
- **Connected / Wi-Fi / Wired / Guest clients** sensors.
- **CPU usage, Memory usage, WAN IP address, WAN uptime** (diagnostic). These are created only if
  your router reports them.
- **Refresh data** button. Polls the router immediately, for when you want fresh metrics without
  waiting for the hourly poll.
- **Restart** button.
- **Guest Wi-Fi 5 GHz** switch. Turns the 5 GHz guest network on and off.
- **New guest 5 GHz password** button. Sets a random 8-character password (letters and digits) on
  the 5 GHz guest network.
- **Guest 5 GHz password** and **Guest 5 GHz SSID** sensors. Show the current guest password and
  network name, so you can read them off the device page.

The guest entities are created only if the router exposes the `guest_5g` wireless form (Archer
AXE75 / AXE5400 and similar). Password changes are read back from the router before they're
shown, so the sensor always reflects what the router actually stored.

The integration polls once an hour. Each poll logs in, reads the data and logs out.

## How it avoids breaking Home Assistant

- `tplinkrouterc6u` is synchronous (it uses `requests`). Every call runs in HA's executor through
  `hass.async_add_executor_job`, never on the event loop.
- Every network operation has a timeout: 10 s per HTTP request, 45 s per poll, 180 s for model
  detection, and a 5 s TCP pre-check, so a wrong IP fails in seconds instead of minutes.
- A lock serializes access, so a poll and a reboot never fight over the single router session.
- If the router is offline at startup, the entry goes into *retry* mode in the background instead
  of failing. Unexpected library exceptions are caught and reported as failed updates.
- If the password is wrong, polling stops and HA asks you to re-enter it, so it won't keep
  failing logins and get you locked out of the router.
