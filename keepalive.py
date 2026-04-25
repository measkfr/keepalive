import os
import sys
import time
import json
import threading
import logging
import socket
import struct
import random
import ssl
import requests
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
# PROXYLESS STEALTH VOICE CONNECTION (FOR ACCOUNT 2)
# ============================================================
class ProxylessStealthVoice:
    def __init__(self, token, guild_id, channel_id, account_name):
        self.token = token
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.account_name = account_name

        self.gateway_ws = None
        self.voice_ws = None
        self.udp_socket = None
        self.voice_udp_port = None
        self.voice_udp_ip = None
        self.ssrc = None

        self.session_id = None
        self.user_id = None
        self.endpoint = None
        self.voice_token = None
        self.running = True
        self.connected_voice = False
        self.gateway_connected = False

        self.voice_sequence = 0
        self.voice_timestamp = 0
        self.lock = threading.Lock()

        self.deep_stealth = True
        self.send_silence = True

    def _create_ssl_context(self):
        ctx = ssl.create_default_context()
        if self.deep_stealth:
            ciphers = [
                'ECDHE-ECDSA-AES128-GCM-SHA256',
                'ECDHE-RSA-AES128-GCM-SHA256',
                'ECDHE-ECDSA-AES256-GCM-SHA384',
                'ECDHE-RSA-AES256-GCM-SHA384'
            ]
            random.shuffle(ciphers)
            ctx.set_ciphers(':'.join(ciphers))
        return ctx

    def start(self):
        threading.Thread(target=self._gateway_loop, daemon=True).start()
        threading.Thread(target=self._monitor_loop, daemon=True).start()

    def _gateway_loop(self):
        while self.running:
            try:
                ws_url = "wss://gateway.discord.gg/?v=9&encoding=json"
                self.gateway_ws = websocket.WebSocketApp(
                    ws_url,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close
                )
                if self.deep_stealth:
                    time.sleep(random.uniform(0.5, 2.5))
                ssl_context = self._create_ssl_context() if self.deep_stealth else None
                self.gateway_ws.run_forever(
                    ping_interval=30,
                    ping_timeout=10,
                    sslopt={"context": ssl_context} if ssl_context else {}
                )
            except Exception as e:
                logger.error(f"🎙️ [{self.account_name}] Gateway loop error: {e}")
            if self.running:
                delay = random.uniform(2, 7) if self.deep_stealth else 5
                time.sleep(delay)

    def _on_open(self, ws):
        self.gateway_connected = True
        logger.info(f"🎙️ [{self.account_name}] Gateway open (proxyless stealth)")
        ua_list = ["linux", "windows", "macos", "android", "ios"]
        device_list = ["Discord", "DiscordClient", "BetterDiscord", "WebDiscord"]
        chosen_ua = random.choice(ua_list) if self.deep_stealth else "linux"
        chosen_device = random.choice(device_list) if self.deep_stealth else "DCKeepAlive"
        identify = {
            "op": 2,
            "d": {
                "token": self.token,
                "properties": {"$os": chosen_ua, "$browser": chosen_device, "$device": chosen_device},
                "presence": {"status": "online", "activities": [{"name": "VC", "type": 0}], "afk": False}
            }
        }
        time.sleep(random.uniform(0.1, 0.5))
        ws.send(json.dumps(identify))

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
            op = data.get('op')
            t = data.get('t')
            d = data.get('d', {})

            if op == 10:
                interval = d['heartbeat_interval']
                if self.deep_stealth:
                    interval = int(interval * random.uniform(0.85, 1.15))
                threading.Thread(target=self._gateway_heartbeat, args=(ws, interval), daemon=True).start()
            elif t == 'READY':
                self.user_id = d['user']['id']
                logger.info(f"🎙️ [{self.account_name}] Ready, user_id={self.user_id}")
                time.sleep(random.uniform(0.2, 1.0))
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
        ssl_context = self._create_ssl_context() if self.deep_stealth else None
        threading.Thread(target=lambda: self.voice_ws.run_forever(
            ping_interval=30,
            ping_timeout=10,
            sslopt={"context": ssl_context} if ssl_context else {}
        ), daemon=True).start()

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
            if op == 2:
                self.ssrc = d['ssrc']
                self.voice_udp_ip = d['ip']
                self.voice_udp_port = d['port']
                self._udp_discovery()
                self.connected_voice = True
                logger.info(f"✅ [{self.account_name}] In VC (deafened, stealth)")
                if self.send_silence:
                    threading.Thread(target=self._send_silence_opus, daemon=True).start()
            elif op == 8:
                interval = d.get('heartbeat_interval', 41250) / 1000
                if self.deep_stealth:
                    interval = interval * random.uniform(0.9, 1.1)
                threading.Thread(target=self._voice_heartbeat, args=(ws, interval), daemon=True).start()
        except Exception as e:
            logger.error(f"🎙️ [{self.account_name}] Voice msg error: {e}")

    def _udp_discovery(self):
        try:
            self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            packet = struct.pack('>I', self.ssrc) + b'\x00' * 70
            self.udp_socket.sendto(packet, (self.voice_udp_ip, self.voice_udp_port))
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

    def _send_silence_opus(self):
        time.sleep(2)
        while self.running and self.connected_voice and self.udp_socket:
            silence_frame = b'\xf8\xff\xfe'
            packet = struct.pack('>I', self.ssrc) + silence_frame
            try:
                self.udp_socket.sendto(packet, (self.voice_udp_ip, self.voice_udp_port))
            except:
                pass
            time.sleep(random.uniform(3, 7))

    def _gateway_heartbeat(self, ws, interval_ms):
        interval = interval_ms / 1000
        while self.running and ws.sock and ws.sock.connected:
            sleep_time = interval + (random.uniform(-1, 1) if self.deep_stealth else 0)
            time.sleep(max(0.5, sleep_time))
            try:
                ws.send(json.dumps({"op": 1, "d": None}))
            except:
                break

    def _voice_heartbeat(self, ws, interval):
        while self.running and ws.sock and ws.sock.connected:
            sleep_time = interval + (random.uniform(-0.3, 0.3) if self.deep_stealth else 0)
            time.sleep(max(0.5, sleep_time))
            try:
                ws.send(json.dumps({"op": 3, "d": int(time.time() * 1000)}))
            except:
                break

    def _monitor_loop(self):
        while self.running:
            time.sleep(30)
            if not self.connected_voice and self.gateway_ws and self.gateway_ws.sock:
                logger.warning(f"⚠️ [{self.account_name}] Voice lost, rejoining...")
                with self.lock:
                    self._join_voice(self.gateway_ws)

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
# DEEP STEALTH DISCORD CLIENT (ACCOUNT 2)
# ============================================================
class DeepStealthClient:
    def __init__(self, token, account_name, fixed_status=None, rotating_statuses=None, interval_minutes=30):
        self.token = token
        self.account_name = account_name
        self.fixed_status = fixed_status
        self.rotating_statuses = rotating_statuses
        self.base_interval = interval_minutes * 60
        self.current_index = 0

        self.ws = None
        self.sequence = None
        self.session_id = None
        self.running = True

        self.voice_conn = None
        self.voice_enabled = False
        self.voice_guild_id = None
        self.voice_channel_id = None

        self.deep_undetectable = True
        self.simulate_typing = False
        self.fake_cdn_requests = True
        self.random_heartbeat = True
        self.random_reconnect = True
        self.random_status_interval = True

    def set_voice(self, enabled, guild_id, channel_id):
        self.voice_enabled = enabled
        self.voice_guild_id = guild_id
        self.voice_channel_id = channel_id

    def start(self):
        threading.Thread(target=self._main_loop, daemon=True).start()
        if self.fake_cdn_requests:
            threading.Thread(target=self._cdn_emulation, daemon=True).start()

    def _create_ssl_context(self):
        ctx = ssl.create_default_context()
        if self.deep_undetectable:
            ciphers = [
                'ECDHE-ECDSA-AES128-GCM-SHA256',
                'ECDHE-RSA-AES128-GCM-SHA256',
                'ECDHE-ECDSA-AES256-GCM-SHA384',
                'ECDHE-RSA-AES256-GCM-SHA384'
            ]
            random.shuffle(ciphers)
            ctx.set_ciphers(':'.join(ciphers))
        return ctx

    def _main_loop(self):
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
                if self.random_reconnect:
                    time.sleep(random.uniform(0.5, 3))
                ssl_context = self._create_ssl_context() if self.deep_undetectable else None
                self.ws.run_forever(
                    ping_interval=30,
                    ping_timeout=10,
                    sslopt={"context": ssl_context} if ssl_context else {}
                )
            except Exception as e:
                logger.error(f"💥 [{self.account_name}] Connection error: {e}")
            if self.running:
                delay = random.uniform(3, 8) if self.random_reconnect else reconnect_delay
                time.sleep(delay)
                reconnect_delay = min(reconnect_delay * 2, 60) if not self.random_reconnect else reconnect_delay

    def _on_open(self, ws):
        logger.info(f"✅ [{self.account_name}] Gateway connected (proxyless deep stealth)")
        self._identify(ws)

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
            op = data.get('op')
            t = data.get('t')
            d = data.get('d', {})
            if data.get('s'):
                self.sequence = data['s']

            # Block all system DMs and security alerts
            if t == 'MESSAGE_CREATE':
                author = d.get('author', {})
                content = d.get('content', '')
                if author.get('id') == '1':
                    logger.info(f"🛡️ [{self.account_name}] Blocked system message (silent)")
                    return
                keywords = ['suspicious', 'compromised', 'security alert', 'password reset', 'account disabled', 
                           'phishing', 'unauthorized', 'breach', 'hack', 'token', 'violation', 'tos violation']
                if any(k in content.lower() for k in keywords):
                    logger.info(f"🛡️ [{self.account_name}] Blocked security-related message")
                    return

            if op == 10:
                interval = d['heartbeat_interval']
                if self.random_heartbeat:
                    interval = int(interval * random.uniform(0.85, 1.15))
                threading.Thread(target=self._heartbeat_loop, args=(ws, interval), daemon=True).start()
                if self.rotating_statuses:
                    threading.Thread(target=self._rotation_loop, daemon=True).start()
                if self.fixed_status:
                    threading.Thread(target=self._status_refresh, daemon=True).start()
            elif op == 0:
                if t == 'READY':
                    self.session_id = d.get('session_id')
                    user = d.get('user', {})
                    logger.info(f"🎉 [{self.account_name}] Logged in as {user.get('username')}")
                    if self.fixed_status:
                        self._update_status(self.fixed_status)
                    elif self.rotating_statuses:
                        self._update_status(self.rotating_statuses[0])
                    if self.voice_enabled:
                        time.sleep(random.uniform(0.5, 2))
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

        if self.deep_undetectable:
            random_presence = random.choice(['online', 'idle', 'dnd'])
            random_activity_type = random.choice([0, 1, 2, 3, 4])
        else:
            random_presence = 'online'
            random_activity_type = 0

        ua_list = ["linux", "windows", "macos", "android", "ios"]
        dev_list = ["Discord", "DiscordClient", "BetterDiscord", "WebDiscord", "DiscordCanary"]
        chosen_ua = random.choice(ua_list) if self.deep_undetectable else "linux"
        chosen_dev = random.choice(dev_list) if self.deep_undetectable else "Discord"

        payload = {
            "op": 2,
            "d": {
                "token": self.token,
                "properties": {"$os": chosen_ua, "$browser": chosen_dev, "$device": chosen_dev},
                "presence": {
                    "status": random_presence,
                    "activities": [{"name": status, "type": random_activity_type}],
                    "afk": False
                }
            }
        }
        time.sleep(random.uniform(0.1, 0.5))
        ws.send(json.dumps(payload))
        logger.info(f"📨 [{self.account_name}] Identify sent (stealth: {random_presence}/{random_activity_type}) - Status: {status}")

    def _update_status(self, status_text):
        try:
            if self.deep_undetectable:
                random_presence = random.choice(['online', 'idle', 'dnd'])
                random_activity_type = random.choice([0, 1, 2, 3, 4])
            else:
                random_presence = 'online'
                random_activity_type = 0

            payload = {
                "op": 3,
                "d": {
                    "since": 0,
                    "activities": [{"name": status_text, "type": random_activity_type}],
                    "status": random_presence,
                    "afk": False
                }
            }
            if self.ws and self.ws.sock and self.ws.sock.connected:
                if self.deep_undetectable:
                    time.sleep(random.uniform(0.2, 0.8))
                self.ws.send(json.dumps(payload))
                logger.info(f"{'💰' if self.fixed_status else '🔄'} [{self.account_name}] Status: {status_text} (stealth: {random_presence})")
        except Exception as e:
            logger.error(f"Status update error: {e}")

    def _status_refresh(self):
        time.sleep(60)
        while self.running:
            if self.random_status_interval:
                sleep_time = random.randint(1500, 2100)
            else:
                sleep_time = 1800
            time.sleep(sleep_time)
            self._update_status(self.fixed_status)

    def _rotation_loop(self):
        time.sleep(random.uniform(5, 15) if self.deep_undetectable else 10)
        while self.running:
            if self.random_status_interval:
                var_interval = self.base_interval * random.uniform(0.8, 1.2)
                sleep_time = var_interval
            else:
                sleep_time = self.base_interval
            time.sleep(sleep_time)
            self.current_index = (self.current_index + 1) % len(self.rotating_statuses)
            self._update_status(self.rotating_statuses[self.current_index])

    def _start_voice(self):
        self.voice_conn = ProxylessStealthVoice(
            self.token, self.voice_guild_id, self.voice_channel_id, self.account_name
        )
        self.voice_conn.start()

    def _heartbeat_loop(self, ws, interval_ms):
        interval = interval_ms / 1000
        if self.random_heartbeat:
            interval = interval * random.uniform(0.9, 1.1)
        while self.running and ws.sock and ws.sock.connected:
            sleep_time = interval + (random.uniform(-0.5, 0.5) if self.random_heartbeat else 0)
            time.sleep(max(0.5, sleep_time))
            try:
                ws.send(json.dumps({"op": 1, "d": self.sequence}))
            except:
                break

    def _cdn_emulation(self):
        time.sleep(10)
        while self.running:
            try:
                emoji_ids = ["123456789012345678", "876543210987654321"]
                emoji_id = random.choice(emoji_ids)
                url = f"https://cdn.discordapp.com/emojis/{emoji_id}.png"
                requests.get(url, timeout=2, headers={"User-Agent": "Mozilla/5.0"})
                logger.debug(f"🎭 [{self.account_name}] CDN emulation request")
            except:
                pass
            time.sleep(random.randint(300, 600))

    def _on_error(self, ws, error):
        logger.error(f"💥 [{self.account_name}] WS error: {error}")

    def _on_close(self, ws, code, msg):
        logger.warning(f"🔌 [{self.account_name}] Connection closed: {code}")

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
# NORMAL CLIENT FOR ACCOUNT 1 (FIXED VOICE)
# ============================================================
class NormalDiscordClient:
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

            if op == 10:
                interval = d['heartbeat_interval']
                threading.Thread(target=self._heartbeat_loop, args=(ws, interval), daemon=True).start()
                if self.rotating_statuses:
                    threading.Thread(target=self._rotation_loop, daemon=True).start()
                if self.fixed_status:
                    threading.Thread(target=self._status_refresh, daemon=True).start()
            elif op == 0:
                if t == 'READY':
                    self.session_id = d.get('session_id')
                    user = d.get('user', {})
                    logger.info(f"🎉 [{self.account_name}] Logged in as {user.get('username')}")
                    if self.fixed_status:
                        self._update_status(self.fixed_status)
                    elif self.rotating_statuses:
                        self._update_status(self.rotating_statuses[0])
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
        self.voice_conn = NormalVoiceConnection(
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

# IMPROVED NORMAL VOICE CONNECTION FOR ACCOUNT 1 (fixes session invalid)
class NormalVoiceConnection:
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
        self.voice_ws_connected = False

        self.lock = threading.Lock()
        self.reconnect_needed = False

    def start(self):
        threading.Thread(target=self._gateway_loop, daemon=True).start()
        threading.Thread(target=self._monitor_loop, daemon=True).start()

    def _gateway_loop(self):
        while self.running:
            try:
                self.gateway_ws = websocket.WebSocketApp(
                    "wss://gateway.discord.gg/?v=9&encoding=json",
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close
                )
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

            if op == 10:
                interval = d['heartbeat_interval']
                threading.Thread(target=self._gateway_heartbeat, args=(ws, interval), daemon=True).start()
            elif t == 'READY':
                self.user_id = d['user']['id']
                logger.info(f"🎙️ [{self.account_name}] Ready, user_id={self.user_id}")
                # Join voice after ready
                self._join_voice(ws)
            elif t == 'VOICE_STATE_UPDATE':
                if d.get('user_id') == self.user_id:
                    self.session_id = d.get('session_id')
                    logger.info(f"🎙️ [{self.account_name}] Voice state update: session_id={self.session_id}")
            elif t == 'VOICE_SERVER_UPDATE':
                self.endpoint = d.get('endpoint')
                self.voice_token = d.get('token')
                logger.info(f"🎙️ [{self.account_name}] Voice server update: endpoint={self.endpoint}")
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
        self.voice_ws_connected = True

    def _voice_message(self, ws, message):
        try:
            data = json.loads(message)
            op = data.get('op')
            d = data.get('d', {})
            if op == 2:
                self.ssrc = d['ssrc']
                self.voice_ip = d['ip']
                self.voice_port = d['port']
                self._udp_discovery()
                self.connected_voice = True
                logger.info(f"✅ [{self.account_name}] In VC (deafened)")
            elif op == 8:
                interval = d.get('heartbeat_interval', 41250) / 1000
                threading.Thread(target=self._voice_heartbeat, args=(ws, interval), daemon=True).start()
            # Handle session invalid (opcode 4004 or close frame)
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
            # Force rejoin
            self.reconnect_needed = True

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
        while self.running:
            time.sleep(30)
            if not self.connected_voice and self.gateway_ws and self.gateway_ws.sock:
                logger.warning(f"⚠️ [{self.account_name}] Voice lost, rejoining...")
                with self.lock:
                    # Reset voice connection and rejoin
                    if self.voice_ws:
                        self.voice_ws.close()
                    self.connected_voice = False
                    self.voice_ws_connected = False
                    # Re-send voice state update
                    self._join_voice(self.gateway_ws)
            elif self.reconnect_needed:
                self.reconnect_needed = False
                with self.lock:
                    logger.info(f"🎙️ [{self.account_name}] Forcing voice reconnect due to UDP failure")
                    if self.voice_ws:
                        self.voice_ws.close()
                    self.connected_voice = False
                    self._join_voice(self.gateway_ws)

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
        # If we get a "Session is no longer valid" error, flag reconnect
        if "Session is no longer valid" in str(error):
            logger.warning(f"🎙️ [{self.account_name}] Session invalid, will rejoin")
            self.reconnect_needed = True

    def _voice_close(self, ws, code, msg):
        logger.warning(f"🎙️ [{self.account_name}] Voice closed: {code}, msg: {msg}")
        self.connected_voice = False
        self.voice_ws_connected = False
        # If close code indicates session invalid, request rejoin
        if code == 4004 or "Session" in str(msg):
            self.reconnect_needed = True

    def stop(self):
        self.running = False
        if self.udp_socket:
            self.udp_socket.close()
        if self.voice_ws:
            self.voice_ws.close()
        if self.gateway_ws:
            self.gateway_ws.close()

# ============================================================
# HELPER THREADS
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
    print("💰 Account 1: Fucking RICH 💸💸 + VOICE (FIXED)")
    print("🎮 Account 2: 20 GAME STATUSES + DEEP STEALTH + VOICE")
    print("🎙️ Voice: PERMANENT (monitored every 30 sec, auto-rejoin)")
    print("💾 Optimized for Render free tier (512MB)")
    print("=" * 60)

    # Only three environment variables needed
    token_one = os.environ.get('DISCORD_TOKEN_ONE', '').strip()
    token_two = os.environ.get('DISCORD_TOKEN_TWO', '').strip()
    # PORT is already read at the top

    if not token_one and not token_two:
        logger.error("No tokens provided")
        return

    # Hardcoded voice settings – Account One NOW JOINS AND STAYS
    voice_one = True   # <-- FIXED: Account One will join VC
    voice_one_guild = "723642750594973777"
    voice_one_channel = "768103409420337152"

    voice_two = True   # Account Two also joins same channel
    voice_two_guild = "723642750594973777"
    voice_two_channel = "768103409420337152"

    rotation_interval = 30
    rotational_statuses = [
        "Playing Valorant", "Playing Counter-Strike 2", "Playing GTA V",
        "Playing Minecraft", "Playing Fortnite", "Playing Apex Legends",
        "Playing Call of Duty", "Playing League of Legends", "Playing Dota 2",
        "Playing Rocket League", "Playing Among Us", "Playing Fall Guys",
        "Playing Roblox", "Playing Genshin Impact", "Playing Red Dead Redemption 2",
        "Playing The Witcher 3", "Playing Cyberpunk 2077", "Playing Elden Ring",
        "Playing FIFA 24", "Playing Overwatch 2"
    ]

    threading.Thread(target=start_flask, daemon=True).start()
    threading.Thread(target=render_pinger, daemon=True).start()
    time.sleep(2)

    clients = []

    if token_one:
        c1 = NormalDiscordClient(token_one, "ACCOUNT_ONE", fixed_status="Fucking RICH 💸💸")
        c1.set_voice(True, voice_one_guild, voice_one_channel)  # voice enabled
        c1.start()
        clients.append(c1)
        logger.info(f"✅ Account One started with VOICE (guild {voice_one_guild}, channel {voice_one_channel})")

    if token_two:
        c2 = DeepStealthClient(token_two, "ACCOUNT_TWO", rotating_statuses=rotational_statuses, interval_minutes=rotation_interval)
        c2.set_voice(True, voice_two_guild, voice_two_channel)
        c2.start()
        clients.append(c2)
        logger.info(f"✅ Account Two started with 20 GAME STATUSES + STEALTH VOICE")

    logger.info("=" * 60)
    logger.info("🟢 All systems running.")
    logger.info("🔊 Account One is now joined and will automatically rejoin if voice drops.")
    logger.info("🎮 Account Two rotates through 20 game statuses every ~30 minutes.")
    logger.info("🔒 Full proxyless stealth active for Account Two.")
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
