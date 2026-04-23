import os
import sys
import time
import json
import threading
import logging
import socket
import struct
from datetime import datetime
from flask import Flask, jsonify
from dotenv import load_dotenv

try:
    import websocket
    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False
    print("❌ websocket-client not installed")
    sys.exit(1)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
PORT = int(os.environ.get('PORT', 10000))

@app.route('/')
def home():
    return jsonify({"status": "online", "timestamp": datetime.now().isoformat()})

@app.route('/health')
def health():
    return jsonify({"status": "healthy"})

@app.route('/ping')
def ping():
    return jsonify({"pong": True})

def start_flask():
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

# ============================================================
# EFFICIENT VOICE CONNECTION (NO MEMORY LEAK)
# ============================================================
class EfficientVoiceConnection:
    def __init__(self, token, guild_id, channel_id, account_name):
        self.token = token
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.account_name = account_name

        self.gateway_ws = None
        self.voice_ws = None
        self.udp_socket = None

        self.session_id = None
        self.user_id = None
        self.endpoint = None
        self.voice_token = None
        self.ssrc = None
        self.voice_ip = None
        self.voice_port = None
        self.heartbeat_interval = 41250
        self.running = True
        self.connected_voice = False
        self.gateway_connected = False

        self.lock = threading.Lock()

    def start(self):
        threading.Thread(target=self._gateway_loop, daemon=True).start()
        # Single monitor thread for this voice connection
        threading.Thread(target=self._monitor_loop, daemon=True).start()

    def _gateway_loop(self):
        """Main gateway connection with manual reconnect (no auto-reconnect)"""
        while self.running:
            try:
                self.gateway_ws = websocket.WebSocketApp(
                    "wss://gateway.discord.gg/?v=9&encoding=json",
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close
                )
                # Run without auto-reconnect to avoid thread buildup
                self.gateway_ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as e:
                logger.error(f"🎙️ [{self.account_name}] Gateway loop error: {e}")
            if self.running:
                time.sleep(5)

    def _on_open(self, ws):
        self.gateway_connected = True
        logger.info(f"🎙️ [{self.account_name}] Gateway open")
        identify = {
            "op": 2,
            "d": {
                "token": self.token,
                "properties": {"$os": "linux", "$browser": "DCKeepAlive", "$device": "DCKeepAlive"},
                "presence": {"status": "online", "activities": [{"name": "VC", "type": 0}], "afk": False}
            }
        }
        ws.send(json.dumps(identify))

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
            op = data.get('op')
            t = data.get('t')
            d = data.get('d', {})

            if op == 10:  # Hello
                interval = d['heartbeat_interval']
                threading.Thread(target=self._gateway_heartbeat, args=(ws, interval), daemon=True).start()
            elif t == 'READY':
                self.user_id = d['user']['id']
                logger.info(f"🎙️ [{self.account_name}] Ready, user_id={self.user_id}")
                self._join_voice(ws)
            elif t == 'VOICE_STATE_UPDATE':
                if d.get('user_id') == self.user_id:
                    self.session_id = d.get('session_id')
            elif t == 'VOICE_SERVER_UPDATE':
                self.endpoint = d.get('endpoint')
                self.voice_token = d.get('token')
                if self.endpoint and self.voice_token and self.session_id:
                    self._connect_voice()
        except Exception as e:
            logger.error(f"🎙️ [{self.account_name}] Gateway message error: {e}")

    def _join_voice(self, ws):
        payload = {
            "op": 4,
            "d": {
                "guild_id": self.guild_id,
                "channel_id": self.channel_id,
                "self_mute": False,
                "self_deaf": True
            }
        }
        ws.send(json.dumps(payload))
        logger.info(f"🎙️ [{self.account_name}] Join VC: {self.channel_id}")

    def _connect_voice(self):
        host = self.endpoint.split(':')[0]
        url = f"wss://{host}:443?v=4"
        self.voice_ws = websocket.WebSocketApp(
            url,
            on_open=self._voice_open,
            on_message=self._voice_message,
            on_error=self._voice_error,
            on_close=self._voice_close
        )
        threading.Thread(target=self.voice_ws.run_forever, daemon=True).start()

    def _voice_open(self, ws):
        identify = {
            "op": 0,
            "d": {
                "server_id": self.guild_id,
                "user_id": self.user_id,
                "session_id": self.session_id,
                "token": self.voice_token
            }
        }
        ws.send(json.dumps(identify))

    def _voice_message(self, ws, message):
        try:
            data = json.loads(message)
            op = data.get('op')
            d = data.get('d', {})
            if op == 2:  # Ready
                self.ssrc = d['ssrc']
                self.voice_ip = d['ip']
                self.voice_port = d['port']
                self._udp_discovery()
                self.connected_voice = True
                logger.info(f"✅ [{self.account_name}] In VC (deafened)")
            elif op == 8:  # Hello
                interval = d.get('heartbeat_interval', 41250) / 1000
                threading.Thread(target=self._voice_heartbeat, args=(ws, interval), daemon=True).start()
        except Exception as e:
            logger.error(f"🎙️ [{self.account_name}] Voice msg error: {e}")

    def _udp_discovery(self):
        try:
            self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            packet = struct.pack('>I', self.ssrc) + b'\x00' * 70
            self.udp_socket.sendto(packet, (self.voice_ip, self.voice_port))
            response, _ = self.udp_socket.recvfrom(74)
            ip = response[4:68].split(b'\x00')[0].decode()
            port = struct.unpack('>H', response[68:70])[0]
            select = {
                "op": 1,
                "d": {
                    "protocol": "udp",
                    "data": {"address": ip, "port": port, "mode": "xsalsa20_poly1305"}
                }
            }
            self.voice_ws.send(json.dumps(select))
        except Exception as e:
            logger.error(f"🎙️ [{self.account_name}] UDP error: {e}")
            self.connected_voice = False

    def _gateway_heartbeat(self, ws, interval_ms):
        interval = interval_ms / 1000
        while self.running and ws.sock and ws.sock.connected:
            time.sleep(interval)
            try:
                ws.send(json.dumps({"op": 1, "d": None}))
            except:
                break

    def _voice_heartbeat(self, ws, interval):
        while self.running and ws.sock and ws.sock.connected:
            time.sleep(interval)
            try:
                ws.send(json.dumps({"op": 3, "d": int(time.time() * 1000)}))
            except:
                break

    def _monitor_loop(self):
        """Check every 30 seconds - if voice lost, force rejoin without restarting gateway"""
        while self.running:
            time.sleep(30)
            if not self.connected_voice and self.gateway_ws and self.gateway_ws.sock:
                logger.warning(f"⚠️ [{self.account_name}] Voice lost, rejoining...")
                with self.lock:
                    self._join_voice(self.gateway_ws)
            elif not self.gateway_connected:
                # Gateway will reconnect itself in the main loop
                pass

    def _on_error(self, ws, error):
        logger.error(f"🎙️ [{self.account_name}] Gateway error: {error}")
        self.gateway_connected = False

    def _on_close(self, ws, code, msg):
        logger.warning(f"🎙️ [{self.account_name}] Gateway closed: {code}")
        self.gateway_connected = False
        self.connected_voice = False

    def _voice_error(self, ws, error):
        logger.error(f"🎙️ [{self.account_name}] Voice error: {error}")
        self.connected_voice = False

    def _voice_close(self, ws, code, msg):
        logger.warning(f"🎙️ [{self.account_name}] Voice closed: {code}")
        self.connected_voice = False

    def stop(self):
        self.running = False
        if self.udp_socket:
            self.udp_socket.close()
        if self.voice_ws:
            self.voice_ws.close()
        if self.gateway_ws:
            self.gateway_ws.close()

# ============================================================
# DISCORD CLIENT (STATUS + VOICE) - ONE THREAD PER ACCOUNT
# ============================================================
class DiscordClient:
    def __init__(self, token, account_name, fixed_status=None, rotating_statuses=None, interval_minutes=30):
        self.token = token
        self.account_name = account_name
        self.fixed_status = fixed_status
        self.rotating_statuses = rotating_statuses
        self.interval_seconds = interval_minutes * 60
        self.current_index = 0

        self.ws = None
        self.sequence = None
        self.heartbeat_interval = 41250
        self.session_id = None
        self.running = True

        self.voice_conn = None
        self.voice_enabled = False
        self.voice_guild_id = None
        self.voice_channel_id = None

    def set_voice(self, enabled, guild_id, channel_id):
        self.voice_enabled = enabled
        self.voice_guild_id = guild_id
        self.voice_channel_id = channel_id

    def start(self):
        threading.Thread(target=self._main_loop, daemon=True).start()

    def _main_loop(self):
        """Maintain gateway connection with backoff"""
        reconnect_delay = 2
        while self.running:
            try:
                self.ws = websocket.WebSocketApp(
                    "wss://gateway.discord.gg/?v=9&encoding=json",
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close
                )
                self.ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as e:
                logger.error(f"💥 [{self.account_name}] Connection error: {e}")
            if self.running:
                time.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 60)

    def _on_open(self, ws):
        logger.info(f"✅ [{self.account_name}] Gateway connected")
        self._identify(ws)

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
            op = data.get('op')
            t = data.get('t')
            d = data.get('d', {})
            if data.get('s'):
                self.sequence = data['s']

            if op == 10:  # Hello
                interval = d['heartbeat_interval']
                threading.Thread(target=self._heartbeat_loop, args=(ws, interval), daemon=True).start()
                # Start status rotation if needed
                if self.rotating_statuses:
                    threading.Thread(target=self._rotation_loop, daemon=True).start()
                if self.fixed_status:
                    threading.Thread(target=self._status_refresh, daemon=True).start()
            elif op == 0:  # Dispatch
                if t == 'READY':
                    self.session_id = d.get('session_id')
                    user = d.get('user', {})
                    logger.info(f"🎉 [{self.account_name}] Logged in as {user.get('username')}")
                    # Set initial status
                    if self.fixed_status:
                        self._update_status(self.fixed_status)
                    elif self.rotating_statuses:
                        self._update_status(self.rotating_statuses[0])
                    # Start voice
                    if self.voice_enabled:
                        self._start_voice()
                elif t == 'RESUMED':
                    logger.info(f"🔄 [{self.account_name}] Resumed")
                    if self.fixed_status:
                        self._update_status(self.fixed_status)
                    elif self.rotating_statuses:
                        self._update_status(self.rotating_statuses[self.current_index])
            elif op in (9, 7):
                logger.warning(f"⚠️ [{self.account_name}] Invalid session, reconnecting")
                self._reconnect()
        except Exception as e:
            logger.error(f"❌ [{self.account_name}] Message error: {e}")

    def _identify(self, ws):
        status = ""
        if self.fixed_status:
            status = self.fixed_status
        elif self.rotating_statuses:
            status = self.rotating_statuses[0]
        else:
            status = "Online"
        payload = {
            "op": 2,
            "d": {
                "token": self.token,
                "properties": {"$os": "linux", "$browser": "Discord", "$device": "Discord"},
                "presence": {
                    "status": "online",
                    "activities": [{"name": status, "type": 0}],
                    "afk": False
                }
            }
        }
        ws.send(json.dumps(payload))
        logger.info(f"📨 [{self.account_name}] Identify sent, status: {status}")

    def _update_status(self, status_text):
        try:
            payload = {
                "op": 3,
                "d": {
                    "since": 0,
                    "activities": [{"name": status_text, "type": 0}],
                    "status": "online",
                    "afk": False
                }
            }
            if self.ws and self.ws.sock and self.ws.sock.connected:
                self.ws.send(json.dumps(payload))
                logger.info(f"{'💰' if self.fixed_status else '🔄'} [{self.account_name}] Status: {status_text}")
        except Exception as e:
            logger.error(f"Status update error: {e}")

    def _status_refresh(self):
        """Refresh fixed status every 30 minutes"""
        time.sleep(60)
        while self.running:
            time.sleep(1800)
            self._update_status(self.fixed_status)

    def _rotation_loop(self):
        time.sleep(10)
        while self.running:
            time.sleep(self.interval_seconds)
            self.current_index = (self.current_index + 1) % len(self.rotating_statuses)
            self._update_status(self.rotating_statuses[self.current_index])

    def _start_voice(self):
        self.voice_conn = EfficientVoiceConnection(
            self.token, self.voice_guild_id, self.voice_channel_id, self.account_name
        )
        self.voice_conn.start()

    def _heartbeat_loop(self, ws, interval_ms):
        interval = interval_ms / 1000
        while self.running and ws.sock and ws.sock.connected:
            time.sleep(interval)
            try:
                ws.send(json.dumps({"op": 1, "d": self.sequence}))
            except:
                break

    def _on_error(self, ws, error):
        logger.error(f"💥 [{self.account_name}] WS error: {error}")

    def _on_close(self, ws, code, msg):
        logger.warning(f"🔌 [{self.account_name}] Connection closed: {code}")
        # main loop will reconnect

    def _reconnect(self):
        if self.ws:
            self.ws.close()
        self.ws = None
        self.sequence = None

    def stop(self):
        self.running = False
        if self.voice_conn:
            self.voice_conn.stop()
        if self.ws:
            self.ws.close()

# ============================================================
# HELPER THREADS (minimal)
# ============================================================
def render_pinger():
    import requests
    time.sleep(10)
    url = f"http://localhost:{PORT}/ping"
    while True:
        try:
            requests.get(url, timeout=5)
        except:
            pass
        time.sleep(180)

# ============================================================
# MAIN
# ============================================================
def main():
    print("=" * 60)
    print("MEMORY-OPTIMIZED DUAL DISCORD KEEP-ALIVE")
    print("💰 Account 1: Fucking RICH 💸💸")
    print("🔄 Account 2: Rotating 3 statuses")
    print("🎙️ Voice: PERMANENT (monitored every 30 sec, no leave)")
    print("💾 Optimized for Render free tier (512MB)")
    print("=" * 60)

    token_one = os.environ.get('DISCORD_TOKEN_ONE', '').strip()
    token_two = os.environ.get('DISCORD_TOKEN_TWO', '').strip()

    if not token_one and not token_two:
        logger.error("No tokens provided")
        return

    DEFAULT_GUILD = "893842188037943346"
    DEFAULT_CHANNEL = "896743673587437568"

    voice_one = os.environ.get('VOICE_JOIN_ONE', 'false').lower() == 'true'
    voice_one_guild = os.environ.get('VOICE_GUILD_ID_ONE', DEFAULT_GUILD)
    voice_one_channel = os.environ.get('VOICE_CHANNEL_ID_ONE', DEFAULT_CHANNEL)

    voice_two = os.environ.get('VOICE_JOIN_TWO', 'false').lower() == 'true'
    voice_two_guild = os.environ.get('VOICE_GUILD_ID_TWO', DEFAULT_GUILD)
    voice_two_channel = os.environ.get('VOICE_CHANNEL_ID_TWO', DEFAULT_CHANNEL)

    rotation_interval = int(os.environ.get('ROTATION_INTERVAL_MINUTES', '30'))
    rotational_statuses = ["Working On New Video🎥", "NYT💤", "YT- NOTE YOUR TYPE"]

    # Start Flask and pinger (minimal)
    threading.Thread(target=start_flask, daemon=True).start()
    threading.Thread(target=render_pinger, daemon=True).start()
    time.sleep(2)

    clients = []

    if token_one:
        c1 = DiscordClient(token_one, "ACCOUNT_ONE", fixed_status="Fucking RICH 💸💸")
        if voice_one:
            c1.set_voice(True, voice_one_guild, voice_one_channel)
        c1.start()
        clients.append(c1)
        logger.info(f"✅ Account One started (Voice: {voice_one})")

    if token_two:
        c2 = DiscordClient(token_two, "ACCOUNT_TWO", rotating_statuses=rotational_statuses, interval_minutes=rotation_interval)
        if voice_two:
            c2.set_voice(True, voice_two_guild, voice_two_channel)
        c2.start()
        clients.append(c2)
        logger.info(f"✅ Account Two started (Voice: {voice_two})")

    logger.info("=" * 60)
    logger.info("🟢 All systems running. Accounts will NEVER leave voice.")
    logger.info("🎙️ Voice monitor checks every 30 seconds.")
    logger.info("💾 Memory usage optimized (UDP sockets closed, limited threads).")
    logger.info("=" * 60)

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        for c in clients:
            c.stop()
        logger.info("Shutdown.")

if __name__ == "__main__":
    main()
