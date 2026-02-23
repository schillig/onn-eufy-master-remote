import subprocess
import sys
from ppadb.client import Client as AdbClient

# Android KeyEvents Reference
# Home: 3, Back: 4, Up: 19, Down: 20, Left: 21, Right: 22, Select: 66, VolUp: 24, VolDown: 25
KEY_MAP = {
    "home": 3,
    "back": 4,
    "up": 19,
    "down": 20,
    "left": 21,
    "right": 22,
    "enter": 66,
    "vol_up": 24,
    "vol_down": 25,
    "power": 26
}

def connect_to_tv(ip="192.168.50.94", port=5555):
    # Ensure the ADB server is running on your Linux Mint PC
    subprocess.run(["adb", "start-server"], capture_output=True)
    
    client = AdbClient(host="127.0.0.1", port=5037)
    
    # Attempt to connect to the network device
    target = f"{ip}:{port}"
    subprocess.run(["adb", "connect", target], capture_output=True)
    
    device = client.device(target)
    
    if device:
        print(f"Successfully connected to: {ip}")
        # Get device model for confirmation
        model = device.shell("getprop ro.product.model").strip()
        print(f"Device Model: {model}")
        return device
    else:
        print(f"Failed to connect to {ip}. Check if Wireless Debugging is ON.")
        return None

# Execution Logic
if __name__ == "__main__":
    tv = connect_to_tv()
    if tv:
        # Test: Send 'Home' command
        print("Sending 'Home' command...")
        tv.shell(f"input keyevent {KEY_MAP['home']}")
