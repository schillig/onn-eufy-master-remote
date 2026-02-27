import sys
import subprocess
import json
import os
import time
import threading
import websocket
from PyQt6.QtWidgets import (QApplication, QWidget, QPushButton, QGridLayout, 
                             QVBoxLayout, QLabel, QHBoxLayout, QInputDialog, QLineEdit, QMenu,
                             QSystemTrayIcon, QStyle)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QIcon

# Silence the accessibility warning in the terminal
os.environ["QT_LINUX_ACCESSIBILITY_ALWAYS_ON"] = "0"

try:
    from ppadb.client import Client as AdbClient
except ImportError:
    print("Error: adb-shell/pure-python-adb not found. Run: pip install pure-python-adb")

try:
    from spellchecker import SpellChecker
    spell = SpellChecker()
except ImportError:
    spell = None

CONFIG_FILE = os.path.expanduser("~/.onn_remote_config.json")
RECORD_DIR = os.path.expanduser("~/Videos/Eufy_Records")

if not os.path.exists(RECORD_DIR):
    os.makedirs(RECORD_DIR)

APP_MAP = {
    "Netflix": "am start -n com.netflix.ninja/.MainActivity",
    "Hulu": "am start -a android.intent.action.MAIN -c android.intent.category.LEANBACK_LAUNCHER -p com.hulu.livingroomplus",
    "YouTube": "am start -a android.intent.action.MAIN -c android.intent.category.LEANBACK_LAUNCHER -p com.google.android.youtube.tv",
    "Prime": "monkey -p com.amazon.amazonvideo.livingroom 1 || am start -n com.amazon.amazonvideo.livingroom/com.amazon.ignition.IgnitionActivity"
}

class HistoryLineEdit(QLineEdit):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.history = []
        self.history_index = -1
        self.current_text = ""

    def add_to_history(self, text):
        if text and (not self.history or self.history[-1] != text):
            self.history.append(text)
        self.history_index = len(self.history)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Up:
            if self.history and self.history_index > 0:
                if self.history_index == len(self.history):
                    self.current_text = self.text()
                self.history_index -= 1
                self.setText(self.history[self.history_index])
        elif event.key() == Qt.Key.Key_Down:
            if self.history and self.history_index < len(self.history) - 1:
                self.history_index += 1
                self.setText(self.history[self.history_index])
            elif self.history_index == len(self.history) - 1:
                self.history_index += 1
                self.setText(self.current_text) 
        else:
            super().keyPressEvent(event)

class EufyWebsocketWorker(QThread):
    motion_signal = pyqtSignal(bool)
    log_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.ws = None
        self.running = True
        self.ffmpeg_process = None
        self.recording_active = False
        self.serial_number = None
        self.retry_timer = None
        self.stop_timer = None
        self.retries = 0

    def run(self):
        websocket.enableTrace(False)
        while self.running:
            self.ws = websocket.WebSocketApp(
                "ws://127.0.0.1:3000",
                on_open=self.on_open,
                on_message=self.on_message,
                on_error=self.on_error,
                on_close=self.on_close
            )
            self.ws.run_forever()
            if self.running:
                self.log_signal.emit("Reconnecting...")
                time.sleep(5)

    def on_open(self, ws):
        self.log_signal.emit("Bridge Linked.")
        ws.send(json.dumps({"messageId": "set_schema", "command": "set_api_schema", "schemaVersion": 21}))
        ws.send(json.dumps({"messageId": "start_listening", "command": "start_listening"}))

    def on_message(self, ws, message):
        data = json.loads(message)
        if data.get("type") == "event":
            event = data.get("event", {})
            event_type = event.get("event")
            
            if event_type == "motion detected" and event.get("state") is True:
                self.serial_number = event.get("serialNumber")
                if not self.recording_active:
                    self.retries = 0
                    self.motion_signal.emit(True)
                    self.log_signal.emit("REC: Waking Camera...")
                    self.request_stream()

            elif event_type == "livestream video data":
                if not self.recording_active:
                    self.start_recording_process()
                if self.ffmpeg_process and self.ffmpeg_process.poll() is None:
                    video_bytes = bytes(event.get("buffer", {}).get("data", []))
                    self.ffmpeg_process.stdin.write(video_bytes)
                    self.ffmpeg_process.stdin.flush()

            elif event_type == "livestream audio data":
                if self.ffmpeg_process and self.ffmpeg_process.poll() is None:
                    audio_bytes = bytes(event.get("buffer", {}).get("data", []))
                    self.ffmpeg_process.stdin.write(audio_bytes)
                    self.ffmpeg_process.stdin.flush()

            elif event_type == "livestream error":
                self.log_signal.emit("P2P Error. Retrying...")

            elif event_type in ["livestream stopped"]:
                self.stop_recording_process()

    def request_stream(self):
        if self.ws and self.serial_number:
            self.ws.send(json.dumps({
                "messageId": "trigger_live",
                "command": "device.start_livestream",
                "serialNumber": self.serial_number
            }))
            if self.retry_timer:
                self.retry_timer.cancel()
            self.retry_timer = threading.Timer(6.0, self.check_retry)
            self.retry_timer.start()

    def check_retry(self):
        if self.running and not self.recording_active and self.serial_number:
            if self.retries < 3:
                self.retries += 1
                self.log_signal.emit(f"REC: Retry Wake Up ({self.retries}/3)...")
                self.request_stream()
            else:
                self.log_signal.emit("Camera Unreachable.")
                self.motion_signal.emit(False)
                self.retries = 0

    def start_recording_process(self):
        if self.retry_timer:
            self.retry_timer.cancel() 
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        filepath = os.path.join(RECORD_DIR, f"eufy_{timestamp}.mp4")
        cmd = ["ffmpeg", "-y", "-i", "pipe:0", "-c", "copy", "-f", "mp4", "-movflags", "frag_keyframe+empty_moov", filepath]
        try:
            self.ffmpeg_process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self.recording_active = True
            self.log_signal.emit("REC: Stream Active")
            if self.stop_timer:
                self.stop_timer.cancel()
            self.stop_timer = threading.Timer(30.0, self.stop_recording_process)
            self.stop_timer.start()
        except Exception as e:
            print(f"FFmpeg Spawn Error: {e}")

    def stop_recording_process(self):
        self.retries = 0
        if self.retry_timer: self.retry_timer.cancel()
        if self.stop_timer: self.stop_timer.cancel()
        if self.recording_active:
            self.recording_active = False
            if self.ffmpeg_process:
                try:
                    self.ffmpeg_process.stdin.close()
                    self.ffmpeg_process.wait(timeout=2)
                except:
                    self.ffmpeg_process.kill()
                self.ffmpeg_process = None
            self.motion_signal.emit(False)
            self.log_signal.emit("Standby.")
            if self.ws and self.serial_number:
                try:
                    self.ws.send(json.dumps({
                        "messageId": "stop_live",
                        "command": "device.stop_livestream",
                        "serialNumber": self.serial_number
                    }))
                except: pass

    def on_error(self, ws, error): self.log_signal.emit(f"Error: {error}")
    def on_close(self, ws, a, b): self.stop_recording_process()

    def stop(self):
        self.running = False
        self.stop_recording_process()
        if self.ws: self.ws.close()

class OnnMasterRemote(QWidget):
    def __init__(self):
        super().__init__()
        self.is_quitting = False  # Track if we are actually closing
        self.load_settings()
        self.client = AdbClient(host="127.0.0.1", port=5037)
        self.device = None
        
        self.monitor_thread = EufyWebsocketWorker()
        self.monitor_thread.motion_signal.connect(self.update_rec_status)
        self.monitor_thread.log_signal.connect(self.update_cam_status)
        self.monitor_thread.start()
        
        self.init_ui()
        self.setup_tray() # Build the system tray icon
        self.connect_to_device()
        
        if self.always_on_top:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)

    def setup_tray(self):
        self.tray_icon = QSystemTrayIcon(self)
        
        # Tap into Linux Mint's native TV/Monitor icon!
        tv_icon = QIcon.fromTheme("video-display")
        self.tray_icon.setIcon(tv_icon)
        self.setWindowIcon(tv_icon) # Fixes the main window taskbar icon too!
        
        tray_menu = QMenu()
        show_action = tray_menu.addAction("Show Remote")
        show_action.triggered.connect(self.show)
        
        hide_action = tray_menu.addAction("Hide to Tray")
        hide_action.triggered.connect(self.hide)
        
        tray_menu.addSeparator()
        
        quit_action = tray_menu.addAction("Quit Application")
        quit_action.triggered.connect(self.quit_app)
        
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()
        
        # Double-clicking the tray icon opens the UI
        self.tray_icon.activated.connect(self.on_tray_click)

    def on_tray_click(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show()
            self.activateWindow()

    def quit_app(self):
        self.is_quitting = True
        if self.monitor_thread:
            self.monitor_thread.stop()
            self.monitor_thread.wait()
        QApplication.quit()

    def load_settings(self):
        self.ip = "192.168.50.94"
        self.always_on_top = False
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    data = json.load(f)
                    self.ip = data.get("ip", self.ip)
                    self.always_on_top = data.get("always_on_top", False)
            except: pass

    def save_settings(self):
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump({"ip": self.ip, "always_on_top": self.always_on_top}, f)
        except Exception as e: print(f"Save error: {e}")

    def init_ui(self):
        self.setWindowTitle("ONN Master Control v1.05")
        self.setFixedWidth(450)
        self.setMinimumWidth(450)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus) 
        self.setStyleSheet("""
            QWidget { background-color: #121212; color: #eee; font-family: 'Segoe UI'; }
            QPushButton { background-color: #333; border-radius: 6px; padding: 10px; border: 1px solid #444; }
            QPushButton:pressed { background-color: #555; }
            QLineEdit { background-color: #222; border: 1px solid #444; padding: 8px; color: #BB86FC; font-size: 14px; }
            QMenu { background-color: #222; border: 1px solid #444; }
            .PowerOn { background-color: #1b5e20; }
            .PowerOff { background-color: #b71c1c; }
            .ActionBtn { background-color: #444; font-weight: bold; }
        """)

        layout = QVBoxLayout()
        sec_layout = QVBoxLayout()
        sec_row = QHBoxLayout()
        
        self.rec_light = QLabel("● REC")
        self.rec_light.setStyleSheet("color: #444; font-weight: bold;")
        self.cam_status = QLabel("Connecting to Bridge...")
        
        sec_row.addWidget(self.rec_light)
        sec_row.addWidget(self.cam_status)
        sec_layout.addLayout(sec_row)
        layout.addLayout(sec_layout)

        p_row = QHBoxLayout()
        btn_on = QPushButton("TV ON"); btn_on.setFocusPolicy(Qt.FocusPolicy.NoFocus); btn_on.setProperty("class", "PowerOn"); btn_on.clicked.connect(self.wake_tv)
        btn_off = QPushButton("TV OFF"); btn_off.setFocusPolicy(Qt.FocusPolicy.NoFocus); btn_off.setProperty("class", "PowerOff"); btn_off.clicked.connect(lambda: self.send_key(223))
        p_row.addWidget(btn_on); p_row.addWidget(btn_off)
        layout.addLayout(p_row)

        status_row = QHBoxLayout()
        self.status_label = QLabel(f"IP: {self.ip}")
        gear = QPushButton("⚙"); gear.setFixedWidth(45); gear.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        menu = QMenu(self)
        ip_act = menu.addAction("Update TV IP"); ip_act.triggered.connect(self.change_ip)
        top_act = menu.addAction("Always On Top"); top_act.setCheckable(True); top_act.setChecked(self.always_on_top); top_act.triggered.connect(self.toggle_always_on_top)
        gear.setMenu(menu)
        status_row.addWidget(self.status_label); status_row.addWidget(gear)
        layout.addLayout(status_row)

        layout.addWidget(QLabel("<b>TYPE TO APP (Netflix):</b>"))
        type_row = QHBoxLayout()
        self.text_input = HistoryLineEdit()
        self.text_input.returnPressed.connect(self.handle_typing)
        btn_spell_app = QPushButton("A✓"); btn_spell_app.setFixedWidth(40); btn_spell_app.setFocusPolicy(Qt.FocusPolicy.NoFocus); btn_spell_app.clicked.connect(lambda: self.run_spellcheck(self.text_input))
        btn_bksp = QPushButton("⌫"); btn_bksp.setFixedWidth(40); btn_bksp.setFocusPolicy(Qt.FocusPolicy.NoFocus); btn_bksp.clicked.connect(lambda: self.send_key(67))
        btn_clear = QPushButton("Clear"); btn_clear.setFixedWidth(60); btn_clear.setFocusPolicy(Qt.FocusPolicy.NoFocus); btn_clear.clicked.connect(self.clear_tv_text)
        type_row.addWidget(self.text_input); type_row.addWidget(btn_spell_app); type_row.addWidget(btn_bksp); type_row.addWidget(btn_clear)
        layout.addLayout(type_row)

        layout.addWidget(QLabel("<b>GLOBAL TV SEARCH:</b>"))
        search_row = QHBoxLayout()
        self.search_input = HistoryLineEdit()
        self.search_input.returnPressed.connect(self.handle_global_search)
        btn_spell_global = QPushButton("A✓"); btn_spell_global.setFixedWidth(40); btn_spell_global.setFocusPolicy(Qt.FocusPolicy.NoFocus); btn_spell_global.clicked.connect(lambda: self.run_spellcheck(self.search_input))
        btn_search = QPushButton("Search"); btn_search.setFixedWidth(70); btn_search.setFocusPolicy(Qt.FocusPolicy.NoFocus); btn_search.clicked.connect(self.handle_global_search)
        search_row.addWidget(self.search_input); search_row.addWidget(btn_spell_global); search_row.addWidget(btn_search)
        layout.addLayout(search_row)

        grid = QGridLayout()
        grid.addWidget(self.create_btn("▲", 19), 0, 1)
        grid.addWidget(self.create_btn("<", 21), 1, 0)
        grid.addWidget(self.create_btn("OK", 66), 1, 1)
        grid.addWidget(self.create_btn(">", 22), 1, 2)
        grid.addWidget(self.create_btn("▼", 20), 2, 1)
        grid.addWidget(self.create_btn("BACK", 4), 3, 0)
        grid.addWidget(self.create_btn("HOME", 3), 3, 2)
        layout.addLayout(grid)

        vol_row = QHBoxLayout()
        vol_row.addWidget(self.create_btn("VOL -", 25)); vol_row.addWidget(self.create_btn("MUTE", 164)); vol_row.addWidget(self.create_btn("VOL +", 24))
        layout.addLayout(vol_row)

        app_grid = QGridLayout()
        for i, (name, cmd) in enumerate(APP_MAP.items()):
            btn = QPushButton(name); btn.setFocusPolicy(Qt.FocusPolicy.NoFocus); btn.clicked.connect(lambda ch, c=cmd: self.launch_app(c))
            app_grid.addWidget(btn, i // 2, i % 2)
        layout.addLayout(app_grid)
        self.setLayout(layout)

    def create_btn(self, text, code):
        btn = QPushButton(text); btn.setFocusPolicy(Qt.FocusPolicy.NoFocus) 
        btn.clicked.connect(lambda: self.send_key(code))
        return btn

    def mousePressEvent(self, event):
        self.setFocus(); super().mousePressEvent(event)

    def toggle_always_on_top(self, checked):
        self.always_on_top = checked
        self.save_settings()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self.always_on_top)
        self.show() 

    def keyPressEvent(self, event):
        if self.text_input.hasFocus() or self.search_input.hasFocus():
            super().keyPressEvent(event)
            return
        key = event.key()
        if key == Qt.Key.Key_Up: self.send_key(19)
        elif key == Qt.Key.Key_Down: self.send_key(20)
        elif key == Qt.Key.Key_Left: self.send_key(21)
        elif key == Qt.Key.Key_Right: self.send_key(22)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter): self.send_key(66)
        elif key == Qt.Key.Key_Escape: self.send_key(4)
        elif key == Qt.Key.Key_Home: self.send_key(3)
        elif key == Qt.Key.Key_Minus: self.send_key(25)
        elif key == Qt.Key.Key_Plus: self.send_key(24)
        else: super().keyPressEvent(event)

    def update_rec_status(self, is_recording):
        color = "#ff1744" if is_recording else "#444"
        self.rec_light.setStyleSheet(f"color: {color}; font-weight: bold;")
        
        if is_recording:
            cmd = (
                'am broadcast -a de.cyberdream.androidtv.notifications.google.SEND '
                '--es title "SECURITY ALERT" '
                '--es msg "Motion detected at the front door!" '
                '--ei type 2 '
                '--ei duration 8 '
                '--ei position 2'
            )
            try:
                if not self.device: self.connect_to_device()
                if self.device: self.device.shell(cmd)
            except Exception as e:
                print(f"Failed to send TV notification: {e}")

    def update_cam_status(self, msg): 
        self.cam_status.setText(msg)
    
    def run_spellcheck(self, input_widget):
        if not spell: return
        text = input_widget.text()
        if not text: return
        words = text.split()
        corrected = [spell.correction(w) or w for w in words]
        input_widget.setText(" ".join(corrected))

    def handle_typing(self):
        text = self.text_input.text()
        if text:
            self.text_input.add_to_history(text)
            formatted_text = text.replace(' ', '%s')
            cmd = f"input text {formatted_text} && sleep 0.5 && input keyevent 4"
            try:
                if not self.device: self.connect_to_device()
                self.device.shell(cmd)
            except:
                self.connect_to_device()
                if self.device: self.device.shell(cmd)
            self.text_input.clear(); self.text_input.clearFocus(); self.setFocus()

    def clear_tv_text(self):
        threading.Thread(target=self._clear_thread).start()
        self.setFocus()

    def _clear_thread(self):
        try:
            if not self.device: self.connect_to_device()
            self.device.shell("for i in `seq 1 30`; do input keyevent 67; done")
        except: pass

    def handle_global_search(self):
        text = self.search_input.text()
        if text:
            self.search_input.add_to_history(text)
            try:
                if not self.device: self.connect_to_device()
                self.device.shell(f'am start -a android.search.action.GLOBAL_SEARCH --es query "{text}"')
            except:
                self.connect_to_device()
                if self.device: self.device.shell(f'am start -a android.search.action.GLOBAL_SEARCH --es query "{text}"')
            self.search_input.clear(); self.search_input.clearFocus(); self.setFocus()

    def wake_tv(self):
        try:
            if not self.device: self.connect_to_device()
            self.device.shell("input keyevent 0 && sleep 0.3 && input keyevent 224")
        except:
            self.connect_to_device()
            if self.device: self.device.shell("input keyevent 0 && sleep 0.3 && input keyevent 224")

    def send_key(self, code):
        try:
            if not self.device: self.connect_to_device()
            self.device.shell(f"input keyevent {code}")
        except:
            self.connect_to_device()
            if self.device: self.device.shell(f"input keyevent {code}")

    def launch_app(self, cmd):
        try:
            if not self.device: self.connect_to_device()
            self.device.shell(cmd)
        except:
            self.connect_to_device()
            if self.device: self.device.shell(cmd)

    def change_ip(self):
        new_ip, ok = QInputDialog.getText(self, 'Settings', 'Update TV IP:', text=self.ip)
        if ok and new_ip:
            self.ip = new_ip; self.save_settings(); self.connect_to_device()
        self.setFocus()

    def connect_to_device(self):
        subprocess.run(["adb", "connect", f"{self.ip}:5555"], capture_output=True)
        try:
            self.device = self.client.device(f"{self.ip}:5555")
            self.status_label.setText(f"ONLINE: {self.ip}" if self.device else "OFFLINE")
        except: self.device = None; self.status_label.setText("OFFLINE")

    def closeEvent(self, event):
        if not self.is_quitting:
            # Hide the window instead of quitting
            event.ignore()
            self.hide()
            self.tray_icon.showMessage(
                "ONN Master Remote",
                "App minimized to system tray. Double-click to restore.",
                QSystemTrayIcon.MessageIcon.Information,
                2000
            )
        else:
            # Clean exit if quit was clicked from the tray
            super().closeEvent(event)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # Crucial: Prevent PyQt6 from killing the app when the last window is hidden!
    app.setQuitOnLastWindowClosed(False) 
    
    window = OnnMasterRemote()
    window.show()
    sys.exit(app.exec())
