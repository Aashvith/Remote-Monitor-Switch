"""
setup_plug.py - Reset a Belkin Wemo Mini and join it to your Wi-Fi, 100% locally.

Belkin's cloud is gone, but the plug never needed it for setup: a reset plug
creates its own open Wi-Fi network ("Wemo.Mini.XXXX") and listens at
10.22.22.1. We talk to it there using UPnP/SOAP (XML messages over HTTP),
which is exactly what the old Belkin app did. The pywemo library handles
those messages for us.

Commands:
    python setup_plug.py find               # list Wemo plugs on the current network
    python setup_plug.py reset [--host IP]  # factory-reset a plug that's on your network
    python setup_plug.py setup              # connect a freshly reset plug to home Wi-Fi

Run `setup` in your own PowerShell window: it asks for the Wi-Fi password with
hidden input, and the password is never printed, logged, or saved.
"""

import argparse
import getpass
import logging
import sys

import pywemo
from pywemo.exceptions import (
    APNotFound,
    PyWeMoException,
    ShortPassword,
    SetupException,
)

# When a Wemo is in setup mode it acts as its own access point, always at this IP.
SETUP_MODE_IP = "10.22.22.1"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def connect_by_ip(host):
    """Return a pywemo device object for the plug at `host`, or None.

    1. setup_url_for_address() probes the Wemo ports (49152-49155) to find
       which one answers, giving a URL like http://10.22.22.1:49153/setup.xml
    2. device_from_description() downloads that setup.xml (the plug's
       self-description: name, MAC, serial, list of services) and builds
       the right Python class for it (a Switch, for a Wemo Mini).
    """
    url = pywemo.setup_url_for_address(host)
    if url is None:
        return None
    return pywemo.device_from_description(url)


def describe(device):
    """One readable line of information about a device."""
    return (
        f"'{device.name}'  model={device.model_name}  ip={device.host}:{device.port}"
        f"  mac={device.mac}  serial={device.serial_number}"
        f"  firmware={device.firmware_version}"
    )


def pick_one(devices):
    """If several plugs were found, let the user choose one."""
    if len(devices) == 1:
        return devices[0]
    for i, dev in enumerate(devices, start=1):
        print(f"  [{i}] {describe(dev)}")
    while True:
        choice = input("Pick a device number: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(devices):
            return devices[int(choice) - 1]
        print("  Not a valid number, try again.")


def scan_networks(device):
    """Ask the plug which Wi-Fi networks IT can see.

    The plug's radio is 2.4 GHz only, so this list is a good check: if your
    network isn't here, the plug can't join it (5 GHz-only or out of range).

    GetApList returns text like:
        Page:1/1/3$
        MyHomeWifi|6|100|WPA2PSK/AES,
        Neighbor|11|40|WPA2PSK/TKIPAES,
    i.e. ssid | channel | signal strength | auth/encryption
    """
    raw = device.get_service("WiFiSetup").GetApList()["ApList"]
    networks = {}
    for line in raw.split("\n")[1:]:          # first line is a header
        line = line.strip().rstrip(",")
        if "|" not in line:
            continue
        parts = line.split("|")
        ssid, channel, signal, security = parts[0], parts[1], parts[2], parts[-1]
        if not ssid:
            continue                          # hidden networks have no name
        # Keep the strongest entry if a name appears more than once.
        sig = int(signal) if signal.strip().isdigit() else 0
        if ssid not in networks or sig > networks[ssid][1]:
            networks[ssid] = (channel.strip(), sig, security.strip())
    return sorted(networks.items(), key=lambda item: -item[1][1])


def choose_ssid(device):
    """Show the plug's network scan and let the user pick one."""
    print("\nAsking the plug to scan for Wi-Fi networks...")
    try:
        networks = scan_networks(device)
    except (PyWeMoException, KeyError, AttributeError) as exc:
        print(f"  Scan failed ({exc!r}); you can still type the name manually.")
        networks = []

    if networks:
        print("Networks the plug can see (2.4 GHz only):")
        for i, (ssid, (channel, signal, security)) in enumerate(networks, start=1):
            warn = "   <- unsupported (likely WPA3)" if security == "Unknown" else ""
            print(f"  [{i:2}] {ssid:32} ch {channel:>2}  signal {signal:>3}  {security}{warn}")

    while True:
        answer = input("\nEnter the number of your network, or type its exact name: ").strip()
        if answer.isdigit() and networks and 1 <= int(answer) <= len(networks):
            return networks[int(answer) - 1][0]
        if answer:
            return answer
        print("  Please enter something.")


def ask_password(ssid):
    """Read the Wi-Fi password with hidden input, twice, until both match.

    getpass() turns off echo in the terminal, so the password never appears
    on screen. It lives only in this process's memory; nothing writes it out.
    """
    while True:
        first = getpass.getpass(f"Wi-Fi password for '{ssid}' (hidden): ")
        if len(first) < 8:
            print("  WPA2 passwords are at least 8 characters - try again.")
            continue
        second = getpass.getpass("Type it again to confirm: ")
        if first == second:
            return first
        print("  The two entries didn't match - try again.")


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_find(args):
    """Discover Wemo plugs on whatever network the laptop is connected to.

    Uses SSDP: we multicast an "M-SEARCH" UDP message to 239.255.255.250:1900
    and every UPnP device on the LAN replies with its setup.xml URL.
    """
    print("Searching the local network for Wemo devices (a few seconds)...")
    devices = pywemo.discover_devices()
    if not devices:
        print(
            "No Wemo devices found. Check that:\n"
            "  - the laptop and plug are on the same Wi-Fi network\n"
            "  - you clicked 'Allow' if Windows Firewall asked about Python\n"
            "  - the plug is powered (LED on) and finished booting (~30 s)"
        )
        return 1
    for dev in devices:
        print("  " + describe(dev))
    print(
        "\nTip: in your router's settings, add a DHCP reservation for this MAC"
        "\naddress so the plug always gets the same IP (the web app will need it)."
    )
    return 0


def cmd_reset(args):
    """Factory-reset a plug that is reachable on the network.

    Sends the ReSetup SOAP action with Reset=2 ("clear data + Wi-Fi"), the
    same as "Factory Restore" in the old app. If the plug isn't on your
    network, use the physical button reset instead (see README.md).
    """
    if args.host:
        device = connect_by_ip(args.host)
        devices = [device] if device else []
    else:
        print("Searching for Wemo devices...")
        devices = pywemo.discover_devices()

    if not devices:
        print("No plug found on this network. Use the physical reset button instead (see README.md).")
        return 1

    device = pick_one(devices)
    print(f"\nTarget: {describe(device)}")
    if input("This erases its name, rules and Wi-Fi settings. Type RESET to continue: ").strip() != "RESET":
        print("Cancelled.")
        return 1

    status = device.factory_reset()
    print(f"Plug replied: {status!r}")
    print(
        "It will reboot into setup mode in ~30-60 seconds and broadcast a\n"
        "'Wemo.Mini.XXXX' Wi-Fi network. Join that network, then run:\n"
        "    python setup_plug.py setup"
    )
    return 0


def cmd_setup(args):
    """Join a freshly reset plug to the home Wi-Fi."""
    print(
        "Before continuing, the laptop must be connected to the plug's own\n"
        "open Wi-Fi network, named like 'Wemo.Mini.XXXX' (Windows will say\n"
        "'No internet' - that's expected).\n"
    )
    print(f"Connecting to the plug at {args.host}...")
    device = connect_by_ip(args.host)
    if device is None:
        print(
            f"Couldn't reach a Wemo at {args.host}. Make sure the laptop is joined to\n"
            "the 'Wemo.Mini.XXXX' network and the plug's LED is blinking (setup mode)."
        )
        return 1
    print("Found: " + describe(device))

    ssid = choose_ssid(device)
    password = ask_password(ssid)

    print(f"\nSending Wi-Fi details to the plug (encrypted) and waiting for it to join '{ssid}'...")
    print("This takes 20-60 seconds.\n")
    try:
        # pywemo.setup():
        #  1. rescans and finds `ssid` to learn its channel and security type
        #  2. AES-encrypts the password with a key built from the plug's
        #     MAC address + serial number (the plug decrypts it the same way)
        #  3. sends ConnectHomeNetwork, then polls GetNetworkStatus until the
        #     plug reports 1 = connected
        #  4. sends CloseSetup so the plug leaves setup mode
        device.setup(
            ssid=ssid,
            password=password,
            timeout=30,
            connection_attempts=2,
            _encrypt_method=args.encrypt_method,
        )
    except ShortPassword:
        print("The plug rejected the password as too short (min 8 characters).")
        return 1
    except APNotFound:
        print(
            f"The plug can't see '{ssid}'. Check the spelling (case-sensitive) and that\n"
            "your router broadcasts a 2.4 GHz network the plug is in range of."
        )
        return 1
    except SetupException as exc:
        print(f"Setup failed: {exc}")
        msg = str(exc)
        if "Unknown" in msg or "Encryption" in msg:
            print(
                "Your router may be using WPA3 or WPA2/WPA3 'transition' mode. Set the\n"
                "2.4 GHz network to WPA2-Personal (AES) and try again."
            )
        else:
            print(
                "Most common causes: a mistyped password, or this firmware needs a\n"
                "different password-encryption scheme. Try again, and if it still fails:\n"
                "    python setup_plug.py setup --encrypt-method 2\n"
                "    python setup_plug.py setup --encrypt-method 3"
            )
        return 1
    finally:
        password = None  # drop our reference to the secret as soon as we're done

    print(
        f"\nSuccess! The plug has joined '{ssid}' and left setup mode.\n"
        "Next steps:\n"
        "  1. Reconnect this laptop to your home Wi-Fi (it may do so on its own).\n"
        "  2. Wait ~30 seconds, then run:  python setup_plug.py find\n"
        "  3. In your router, reserve the plug's IP for its MAC address:\n"
        f"     {device.mac}"
    )
    return 0


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Locally reset and set up a Belkin Wemo Mini (no Belkin cloud needed)."
    )
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="show pywemo's detailed protocol logging")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("find", help="list Wemo devices on the current network")

    p_reset = sub.add_parser("reset", help="factory-reset a plug that's on your network")
    p_reset.add_argument("--host", help="plug IP (otherwise auto-discover)")

    p_setup = sub.add_parser("setup", help="join a freshly reset plug to your Wi-Fi")
    p_setup.add_argument("--host", default=SETUP_MODE_IP,
                         help=f"plug IP in setup mode (default {SETUP_MODE_IP})")
    p_setup.add_argument("--encrypt-method", type=int, choices=[1, 2, 3], default=None,
                         help="override pywemo's password-encryption auto-detection "
                              "(only if the default fails)")

    args = parser.parse_args()

    # Show pywemo's own progress messages so you can watch the protocol steps.
    # Keep the HTTP libraries quiet: at debug level they could print request
    # details, and the setup request carries the (encrypted) Wi-Fi password.
    logging.basicConfig(format="  [pywemo] %(message)s", level=logging.WARNING)
    logging.getLogger("pywemo").setLevel(logging.DEBUG if args.verbose else logging.INFO)
    for noisy in ("urllib3", "requests"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    commands = {"find": cmd_find, "reset": cmd_reset, "setup": cmd_setup}
    try:
        return commands[args.command](args)
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
