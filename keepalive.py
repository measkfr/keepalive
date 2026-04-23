import os
import sys
import time
import json
import threading
import logging
from datetime import datetime
from flask import Flask, jsonify
from dotenv import load_dotenv

try:
    import websocket
    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False
    print("Warning: websocket-client not installed")

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
PORT = int(os.environ.get('PORT', 10000))

VOICE_ENABLED_ONE = False
VOICE_ENABLED_TWO = False

@app.route("/")
def home():
    return jsonify({
        "status": "online",
        "service": "dual-discord-keepalive-fixed",
        "timestamp": datetime.now().isoformat(),
        "accounts": {
            "account_one": {"voice_enabled": VOICE_ENABLED_ONE},
            "account_two": {"voice_enabled": VOICE_ENABLED_TWO}
        }
    })

@app.route("/health")
def health():
    return jsonify({"status": "healthy"})

@app.route("/ping")
def ping():
    logger.info(f"Ping received at {datetime.now().strftime('%H:%M:%S')}")
    return jsonify({"pong": True})

def start_flask():
    logger.info(f"🚀 Flask server on port {PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

# ============================
# VOICE CONNECTION (same for both)
# ============================
class VoiceConnection:
    def __init__(self, token, channel_id, account_name):
        self.token = token
        self.channel_id = channel_id
        self.account_name = account_name
        self.ws = None
        self.voice_ws = None
        self.session_id = None
        self.user_id = None
        self.guild_id = None
        self.endpoint = None
        self.running = True

    def connect(self):
        self.ws = websocket.WebSocketApp(
            "wss://gateway.discord.gg/?v=9&encoding=json",
            on_open=self.on_gateway_open,
            on_message=self.on_gateway_message,
            on_error=self.on_gateway_error,
            on_close=self.on_gateway_close
        )
        self.ws.run_forever()

    def on_gateway_open(self, ws):
        logger.info(f"🎙️ [{self.account_name}] Voice gateway open")
        identify = {
            "op": 2,
            "d": {
                "token": self.token,
                "properties": {"$os": "linux", "$browser": "chrome", "$device": "chrome"},
                "presence": {"status": "online", "activities": [{"name": "Voice", "type": 0}]}
            }
        }
        ws.send(json.dumps(identify))

    def on_gateway_message(self, ws, message):
        try:
            data = json.loads(message)
            t = data.get('t')
            d = data.get('d', {})
            if t == 'READY':
                self.user_id = d['user']['id']
                logger.info(f"🎙️ [{self.account_name}] Voice ready, joining channel")
                self.join_voice(ws)
            elif t == 'VOICE_STATE_UPDATE':
                if d.get('user_id') == self.user_id:
                    self.session_id = d.get('session_id')
            elif t == 'VOICE_SERVER_UPDATE':
                self.endpoint = d.get('endpoint')
                token = d.get('token')
                self.guild_id = d.get('guild_id')
                if self.endpoint and token and self.session_id:
                    self.connect_voice_udp(token)
        except Exception as e:
            logger.error(f"🎙️ [{self.account_name}] Gateway msg error: {e}")

    def join_voice(self, ws):
        payload = {
            "op": 4,
            "d": {
                "guild_id": self.guild_id if self.guild_id else "0",
                "channel_id": self.channel_id,
                "self_mute": False,
                "self_deaf": True
            }
        }
        ws.send(json.dumps(payload))
        logger.info(f"🎙️ [{self.account_name}] Joining voice channel {self.channel_id}")

    def connect_voice_udp(self, token):
        if not self.endpoint:
            return
        url = f"wss://{self.endpoint}?v=4"
        self.voice_ws = websocket.WebSocketApp(
            url,
            on_open=lambda ws: self.on_voice_open(ws, token),
            on_message=self.on_voice_message,
            on_error=self.on_voice_error,
            on_close=self.on_voice_close
        )
        threading.Thread(target=self.voice_ws.run_forever, daemon=True).start()

    def on_voice_open(self, ws, token):
        identify = {
            "op": 0,
            "d": {
                "server_id": self.guild_id,
                "user_id": self.user_id,
                "session_id": self.session_id,
                "token": token
            }
        }
        ws.send(json.dumps(identify))
        logger.info(f"✅ [{self.account_name}] Permanently in voice channel")

    def on_voice_message(self, ws, message):
        try:
            data = json.loads(message)
            if data.get('op') == 4:
                ws.send(json.dumps({"op": 3, "d": int(time.time() * 1000)}))
        except:
            pass

    def on_voice_error(self, ws, error):
        logger.error(f"🎙️ [{self.account_name}] Voice error: {error}")

    def on_voice_close(self, ws, code, msg):
        logger.warning(f"🎙️ [{self.account_name}] Voice closed, rejoining...")
        time.sleep(5)
        self.connect()

    def on_gateway_error(self, ws, error):
        logger.error(f"🎙️ [{self.account_name}] Gateway error: {error}")

    def on_gateway_close(self, ws, code, msg):
        logger.warning(f"🎙️ [{self.account_name}] Gateway closed, reconnecting...")
        if self.running:
            time.sleep(5)
            self.connect()

    def start(self):
        threading.Thread(target=self.connect, daemon=True).start()

    def stop(self):
        self.running = False

# ============================
# FIXED STATUS MANAGER (Account One)
# ============================
class FixedStatusManager:
    def __init__(self, token, account_name, fixed_status):
        self.token = token
        self.account_name = account_name
        self.fixed_status = fixed_status
        self.ws = None
        self.sequence = None
        self.heartbeat_interval = 41250
        self.session_id = None
        self.running = True
        self.last_heartbeat_ack = time.time()
        self.reconnect_attempts = 0
        self.voice_conn = None
        self.voice_enabled = False
        self.voice_channel_id = None

    def set_voice(self, enabled, channel_id):
        self.voice_enabled = enabled
        self.voice_channel_id = channel_id

    def connect(self):
        while self.running and self.reconnect_attempts < 10:
            try:
                logger.info(f"🌐 [{self.account_name}] Connecting to Discord Gateway...")
                self.ws = websocket.WebSocketApp(
                    "wss://gateway.discord.gg/?v=9&encoding=json",
                    on_open=self.on_open,
                    on_message=self.on_message,
                    on_error=self.on_error,
                    on_close=self.on_close
                )
                self.ws.run_forever(ping_interval=30, ping_timeout=10, reconnect=5)
                if self.running:
                    self.reconnect_attempts += 1
                    time.sleep(min(2 ** self.reconnect_attempts, 30))
            except Exception as e:
                logger.error(f"💥 [{self.account_name}] Connection error: {e}")
                self.reconnect_attempts += 1
                time.sleep(min(2 ** self.reconnect_attempts, 30))

    def on_open(self, ws):
        self.reconnect_attempts = 0
        logger.info(f"✅ [{self.account_name}] Gateway connected")

    def on_message(self, ws, message):
        try:
            data = json.loads(message)
            op = data.get('op')
            t = data.get('t')
            d = data.get('d', {})
            if data.get('s'):
                self.sequence = data['s']
            if op == 10:
                self.heartbeat_interval = d['heartbeat_interval']
                self.identify()
                if not hasattr(self, 'heartbeat_thread') or not self.heartbeat_thread.is_alive():
                    self.heartbeat_thread = threading.Thread(target=self.heartbeat_loop, daemon=True)
                    self.heartbeat_thread.start()
            elif op == 11:
                self.last_heartbeat_ack = time.time()
            elif op == 0:
                if t == 'READY':
                    self.session_id = d.get('session_id')
                    user = d.get('user', {})
                    logger.info(f"🎉 [{self.account_name}] Ready: {user.get('username')}")
                    if self.voice_enabled and self.voice_channel_id:
                        self.start_voice()
                elif t == 'RESUMED':
                    logger.info(f"🔄 [{self.account_name}] Session resumed")
            elif op == 9 or op == 7:
                logger.warning(f"⚠️ [{self.account_name}] Invalid session, reconnecting")
                time.sleep(2)
                self.resume_or_reconnect()
        except Exception as e:
            logger.error(f"❌ [{self.account_name}] Message error: {e}")

    def on_error(self, ws, error):
        logger.error(f"💥 [{self.account_name}] WebSocket error: {error}")

    def on_close(self, ws, close_status_code, close_msg):
        logger.warning(f"🔌 [{self.account_name}] Connection closed: {close_status_code}")
        if self.running:
            time.sleep(2)

    def start_voice(self):
        self.voice_conn = VoiceConnection(self.token, self.voice_channel_id, self.account_name)
        self.voice_conn.start()

    def send_json(self, data):
        try:
            if self.ws and self.ws.sock and self.ws.sock.connected:
                self.ws.send(json.dumps(data))
                return True
        except:
            pass
        return False

    def identify(self):
        payload = {
            "op": 2,
            "d": {
                "token": self.token,
                "properties": {"$os": "linux", "$browser": "chrome", "$device": "chrome"},
                "presence": {
                    "status": "online",
                    "activities": [{"name": self.fixed_status, "type": 0}],
                    "afk": False
                }
            }
        }
        self.send_json(payload)
        logger.info(f"📨 [{self.account_name}] Identified with status: {self.fixed_status}")

    def resume(self):
        if self.session_id and self.sequence:
            payload = {"op": 6, "d": {"token": self.token, "session_id": self.session_id, "seq": self.sequence}}
            return self.send_json(payload)
        return False

    def resume_or_reconnect(self):
        if not self.resume():
            time.sleep(2)
            self.reconnect()

    def heartbeat_loop(self):
        while self.running:
            time.sleep((self.heartbeat_interval / 1000) * 0.8)
            self.send_json({"op": 1, "d": self.sequence})

    def reconnect(self):
        logger.info(f"🔄 [{self.account_name}] Reconnecting...")
        if self.ws:
            try:
                self.ws.close()
            except:
                pass
        time.sleep(2)
        self.sequence = None
        self.connect()

    def start(self):
        logger.info(f"🚀 [{self.account_name}] Starting (fixed status)")
        threading.Thread(target=self.connect, daemon=True).start()

    def stop(self):
        self.running = False
        if self.voice_conn:
            self.voice_conn.stop()
        if self.ws:
            try:
                self.ws.close()
            except:
                pass

# ============================
# ROTATIONAL STATUS MANAGER (Account Two)
# ============================
class RotationalStatusManager:
    def __init__(self, token, account_name, status_list, interval_minutes=30):
        self.token = token
        self.account_name = account_name
        self.status_list = status_list
        self.interval_seconds = interval_minutes * 60
        self.current_index = 0
        self.ws = None
        self.sequence = None
        self.heartbeat_interval = 41250
        self.session_id = None
        self.running = True
        self.last_heartbeat_ack = time.time()
        self.reconnect_attempts = 0
        self.voice_conn = None
        self.voice_enabled = False
        self.voice_channel_id = None

    def set_voice(self, enabled, channel_id):
        self.voice_enabled = enabled
        self.voice_channel_id = channel_id

    def connect(self):
        while self.running and self.reconnect_attempts < 10:
            try:
                logger.info(f"🔄 [{self.account_name}] Connecting to Discord Gateway...")
                self.ws = websocket.WebSocketApp(
                    "wss://gateway.discord.gg/?v=9&encoding=json",
                    on_open=self.on_open,
                    on_message=self.on_message,
                    on_error=self.on_error,
                    on_close=self.on_close
                )
                self.ws.run_forever(ping_interval=30, ping_timeout=10, reconnect=5)
                if self.running:
                    self.reconnect_attempts += 1
                    time.sleep(min(2 ** self.reconnect_attempts, 30))
            except Exception as e:
                logger.error(f"💥 [{self.account_name}] Connection error: {e}")
                self.reconnect_attempts += 1
                time.sleep(min(2 ** self.reconnect_attempts, 30))

    def on_open(self, ws):
        self.reconnect_attempts = 0
        logger.info(f"✅ [{self.account_name}] Gateway connected")

    def on_message(self, ws, message):
        try:
            data = json.loads(message)
            op = data.get('op')
            t = data.get('t')
            d = data.get('d', {})
            if data.get('s'):
                self.sequence = data['s']
            if op == 10:
                self.heartbeat_interval = d['heartbeat_interval']
                self.identify()
                if not hasattr(self, 'heartbeat_thread') or not self.heartbeat_thread.is_alive():
                    self.heartbeat_thread = threading.Thread(target=self.heartbeat_loop, daemon=True)
                    self.heartbeat_thread.start()
                if not hasattr(self, 'rotation_thread') or not self.rotation_thread.is_alive():
                    self.rotation_thread = threading.Thread(target=self.rotation_loop, daemon=True)
                    self.rotation_thread.start()
            elif op == 11:
                self.last_heartbeat_ack = time.time()
            elif op == 0:
                if t == 'READY':
                    self.session_id = d.get('session_id')
                    user = d.get('user', {})
                    logger.info(f"🎉 [{self.account_name}] Ready: {user.get('username')}")
                    if self.voice_enabled and self.voice_channel_id:
                        self.start_voice()
                    # set first status after ready
                    if self.status_list:
                        self.update_status(self.status_list[0])
                elif t == 'RESUMED':
                    logger.info(f"🔄 [{self.account_name}] Session resumed")
            elif op == 9 or op == 7:
                logger.warning(f"⚠️ [{self.account_name}] Invalid session, reconnecting")
                time.sleep(2)
                self.resume_or_reconnect()
        except Exception as e:
            logger.error(f"❌ [{self.account_name}] Message error: {e}")

    def on_error(self, ws, error):
        logger.error(f"💥 [{self.account_name}] WebSocket error: {error}")

    def on_close(self, ws, close_status_code, close_msg):
        logger.warning(f"🔌 [{self.account_name}] Connection closed: {close_status_code}")
        if self.running:
            time.sleep(2)

    def start_voice(self):
        self.voice_conn = VoiceConnection(self.token, self.voice_channel_id, self.account_name)
        self.voice_conn.start()

    def update_status(self, status_name):
        try:
            payload = {
                "op": 3,
                "d": {
                    "since": 0,
                    "activities": [{"name": status_name, "type": 0}],
                    "status": "online",
                    "afk": False
                }
            }
            if self.send_json(payload):
                logger.info(f"🔄 [{self.account_name}] Status → {status_name}")
                return True
        except Exception as e:
            logger.error(f"Status update error: {e}")
        return False

    def rotation_loop(self):
        time.sleep(10)
        while self.running:
            time.sleep(self.interval_seconds)
            self.current_index = (self.current_index + 1) % len(self.status_list)
            self.update_status(self.status_list[self.current_index])

    def send_json(self, data):
        try:
            if self.ws and self.ws.sock and self.ws.sock.connected:
                self.ws.send(json.dumps(data))
                return True
        except:
            pass
        return False

    def identify(self):
        payload = {
            "op": 2,
            "d": {
                "token": self.token,
                "properties": {"$os": "linux", "$browser": "chrome", "$device": "chrome"},
                "presence": {
                    "status": "online",
                    "activities": [{"name": self.status_list[0] if self.status_list else "Online", "type": 0}],
                    "afk": False
                }
            }
        }
        self.send_json(payload)
        logger.info(f"📨 [{self.account_name}] Identified (rotational)")

    def resume(self):
        if self.session_id and self.sequence:
            payload = {"op": 6, "d": {"token": self.token, "session_id": self.session_id, "seq": self.sequence}}
            return self.send_json(payload)
        return False

    def resume_or_reconnect(self):
        if not self.resume():
            time.sleep(2)
            self.reconnect()

    def heartbeat_loop(self):
        while self.running:
            time.sleep((self.heartbeat_interval / 1000) * 0.8)
            self.send_json({"op": 1, "d": self.sequence})

    def reconnect(self):
        logger.info(f"🔄 [{self.account_name}] Reconnecting...")
        if self.ws:
            try:
                self.ws.close()
            except:
                pass
        time.sleep(2)
        self.sequence = None
        self.connect()

    def start(self):
        logger.info(f"🚀 [{self.account_name}] Starting (rotational)")
        threading.Thread(target=self.connect, daemon=True).start()

    def stop(self):
        self.running = False
        if self.voice_conn:
            self.voice_conn.stop()
        if self.ws:
            try:
                self.ws.close()
            except:
                pass

# ============================
# RENDER PINGER & HEALTH
# ============================
def render_pinger():
    import requests
    time.sleep(5)
    url = f"http://localhost:{PORT}/ping"
    while True:
        try:
            requests.get(url, timeout=10)
        except:
            pass
        time.sleep(180)

def health_monitor():
    time.sleep(10)
    while True:
        logger.info("📊 System running (Fixed + Rotational)")
        time.sleep(3600)

# ============================
# MAIN
# ============================
def main():
    print("=" * 60)
    print("DUAL DISCORD KEEP-ALIVE (FULLY FIXED)")
    print("💰 Account 1: Fucking RICH 💸💸 (FIXED)")
    print("🔄 Account 2: Rotating: Working On New Video🎥 | NYT💤 | YT- NOTE YOUR TYPE")
    print("=" * 60)

    token_one = os.environ.get('DISCORD_TOKEN_ONE')
    token_two = os.environ.get('DISCORD_TOKEN_TWO')

    voice_one = os.environ.get('VOICE_JOIN_ONE', 'false').lower() == 'true'
    voice_channel_one = os.environ.get('VOICE_CHANNEL_ID_ONE')
    voice_two = os.environ.get('VOICE_JOIN_TWO', 'false').lower() == 'true'
    voice_channel_two = os.environ.get('VOICE_CHANNEL_ID_TWO')
    rotation_interval = int(os.environ.get('ROTATION_INTERVAL_MINUTES', '30'))

    rotational_statuses = ["Working On New Video🎥", "NYT💤", "YT- NOTE YOUR TYPE"]

    global VOICE_ENABLED_ONE, VOICE_ENABLED_TWO
    VOICE_ENABLED_ONE = voice_one
    VOICE_ENABLED_TWO = voice_two

    threading.Thread(target=start_flask, daemon=True).start()
    threading.Thread(target=render_pinger, daemon=True).start()
    threading.Thread(target=health_monitor, daemon=True).start()
    time.sleep(3)

    clients = []
    if token_one:
        c1 = FixedStatusManager(token_one, "ACCOUNT_ONE", "Fucking RICH 💸💸")
        c1.set_voice(voice_one, voice_channel_one)
        c1.start()
        clients.append(c1)
        logger.info(f"✅ Account One started - Fixed: Fucking RICH 💸💸, Voice: {voice_one}")
    if token_two:
        c2 = RotationalStatusManager(token_two, "ACCOUNT_TWO", rotational_statuses, rotation_interval)
        c2.set_voice(voice_two, voice_channel_two)
        c2.start()
        logger.info(f"✅ Account Two started - Rotational every {rotation_interval} min, Voice: {voice_two}")

    logger.info("=" * 60)
    logger.info("✅ ALL SYSTEMS ONLINE — NO ERRORS EXPECTED")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        for c in clients:
            c.stop()
        logger.info("Shutdown complete")

if __name__ == "__main__":
    main()
