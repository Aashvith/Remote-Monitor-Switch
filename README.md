# Wemo Controller

A self-hosted controller for a Belkin Wemo Mini smart plug. Belkin shut down
its cloud service and app in January 2026, so this project does everything
locally: plug setup, a web UI with ON/OFF buttons and live status, remote
access over Tailscale, and auto-start on Windows boot.

**Stack:** Python 3.15, Flask, [pywemo](https://github.com/pywemo/pywemo) (UPnP/SOAP over HTTP, SSDP discovery), Tailscale

## Roadmap
- [x] Phase 1: local factory reset and Wi-Fi setup of the plug (`setup_plug.py`)
- [x] Phase 2: Flask web app with ON/OFF buttons and live status (`app.py`)
- [x] Phase 3: remote access from iPhone over Tailscale (`run_tailscale.ps1`)
- [x] Phase 4: auto-start on Windows boot (`install_autostart.ps1`)

## Files
| File | Purpose |
|---|---|
| `setup_plug.py` | Find, reset and Wi-Fi-provision the plug |
| `app.py` | Flask web app and JSON API (`/api/status`, `/api/on`, `/api/off`) |
| `run_tailscale.ps1` | Launches the app bound only to the Tailscale `100.x` address |
| `install_autostart.ps1` | Registers a boot-time Scheduled Task (run as Administrator) |

## Project setup

```powershell
py -3.15 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Setting up the plug

**Requirements:** a 2.4 GHz network using WPA2-Personal (AES). The Wemo Mini
can't join 5 GHz networks or WPA3.

1. **Factory reset the plug.**
   - *Physical:* unplug it, hold the button, plug it back in while still
     holding, and release after about 5 seconds when the LED starts flashing.
   - *Over the network* (only if it's still on your Wi-Fi):
     `.\.venv\Scripts\python.exe setup_plug.py reset`
2. **Wait 30–60 seconds**, then join the laptop to the open Wi-Fi network named
   `Wemo.Mini.XXXX`. Windows will show "No internet", which is expected.
3. **Run setup** in a PowerShell window:
   ```powershell
   .\.venv\Scripts\python.exe setup_plug.py setup
   ```
   Pick your network from the plug's scan and enter the password. The input
   is hidden and the password is never saved.
4. **Reconnect the laptop to your home Wi-Fi** and confirm that the plug shows up:
   ```powershell
   .\.venv\Scripts\python.exe setup_plug.py find
   ```
5. **Reserve the plug's IP** in your router's DHCP settings, using the MAC
   address that `find` printed.

## Running the web app

```powershell
.\.venv\Scripts\python.exe app.py        # http://127.0.0.1:5000, this laptop only
```

Settings are environment variables: `WEMO_HOST` (plug IP), `BIND_HOST`, `BIND_PORT`.
The app has no login, so it binds to localhost by default.

**Remote access:** `run_tailscale.ps1` binds the app to the laptop's Tailscale
address only, so it is invisible on your LAN and reachable just from devices on
your tailnet (e.g. your phone at `http://<tailscale-ip>:5000`).

**Auto-start:** from an Administrator PowerShell run
`powershell -ExecutionPolicy Bypass -File .\install_autostart.ps1`.
Remove with `Unregister-ScheduledTask -TaskName "Wemo Controller" -Confirm:$false`.

## How it works
- In setup mode the plug runs its own access point and serves UPnP at
  `10.22.22.1`. Its `setup.xml` describes the device and its SOAP services.
- The `WiFiSetup` service's `GetApList` action returns the networks the plug can see,
  and `ConnectHomeNetwork` gives it your SSID and password. The password is
  AES-encrypted with a key derived from the plug's MAC address and serial number.
- On the home network, plugs are found with **SSDP**: a UDP multicast
  M-SEARCH to `239.255.255.250:1900`, which UPnP devices answer.

## Troubleshooting
- **`find` shows nothing:** allow Python through Windows Firewall when Windows asks,
  and make sure the laptop and the plug are on the same network.
- **Setup fails with the right password:** retry with
  `setup --encrypt-method 2`, then `--encrypt-method 3`. Different firmware versions
  encrypt the password differently.
- **"Unknown" security or WPA3:** set the 2.4 GHz network to WPA2-Personal (AES).
