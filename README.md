# ONN TV Master Remote + Eufy Security Monitor

A custom-built, Python-based desktop application (Linux) that acts as a master control hub for an ONN Android TV box while actively monitoring a Eufy security camera stream via websockets.

## 🌟 Key Features

* **Smart App Launcher:** Seamlessly launches Netflix, Hulu, YouTube, and Prime Video using native Android TV `LEANBACK_LAUNCHER` intents.
* **Auto-Healing ADB:** Automatically detects dropped network connections (like when the TV enters Doze/Deep Sleep mode) and silently reconnects in the background without throwing errors.
* **Physical Keyboard Passthrough:** Use your physical computer keyboard's Arrow Keys, Enter, Esc, Home, '+', and '-' to navigate the TV directly.
* **Bash-Style History & Spellcheck:** Text input fields remember your previous searches (accessible via Up/Down arrows) and feature a one-click "A✓" button to auto-correct typos before sending them to the TV.
* **Global TV Search:** A dedicated search bar that bypasses locked app keyboards (like YouTube/Hulu) by triggering the Google TV global search overlay.
* **Integrated Eufy Camera Monitor:** Listens to a local `eufy-security-ws` bridge. When motion is detected, it automatically commands the camera to start livestreaming and losslessly pipes the raw H.264/AAC bytes directly into FFmpeg to save a recording.

## 🛠️ Prerequisites

To run this application, your Linux machine needs the following system dependencies installed:
* `adb` (Android Debug Bridge) - Must be enabled and authorized on your ONN TV.
* `ffmpeg` - Required for saving the Eufy video streams.
* [eufy-security-ws](https://github.com/bropat/eufy-security-ws) - A local WebSocket bridge running on `127.0.0.1:3000`.

## 📦 Installation

1. Clone the repository:
   ```bash
   git clone [https://github.com/schillig/onn-eufy-master-remote.git](https://github.com/schillig/onn-eufy-master-remote.git)
   cd onn-eufy-master-remoteThis is a remote control for the ONN 4k TV Box that runs on Linux
