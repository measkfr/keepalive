import os
import sys
import time
import json
import threading
import logging
import socket
import struct
import random
from datetime import datetime
from flask import Flask, jsonify
from dotenv import load_dotenv

try:
    import websocket
    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False
    print("ERROR: websocket-client not installed. Run: pip install websocket-client")
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

@app.route("/")
def home():
    return jsonify({
        "status": "online",
        "service": "discord-voice-keepalive-real",
        "timestamp": datetime.now().isoformat()
    })

@app.route("/health")
def health():
    return jsonify({"status": "healthy"})

@app.route("/ping")
def ping():
    logger.info(f"Ping at {datetime.now().strftime('%H:%M:%S')}")
    return jsonify({"pong": True})

def start_flask():
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

# ============================
# COMPLETE VOICE CONNECTION (REAL JOIN)
# ============================
class RealVoiceConnection:
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
        
    def start(self):
        """Start gateway connection"""
        self.connect_gateway()
        
    def connect_gateway(self):
        """Connect to Discord gateway"""
        self.gateway_ws = websocket.WebSocketApp(
            "wss://gateway.discord.gg/?v=9&encoding=json",
            on_open=self.on_gateway_open,
            on_message=self.on_gateway_message,
            on_error=self.on_gateway_error,
            on_close=self.on_gateway_close
        )
        self.gateway_ws.run_forever()
        
    def on_gateway_open(self, ws):
        logger.info(f"🎙️ [{self.account_name}] Gateway open, identifying")
        identify = {
            "op": 2,
            "d": {
                "token": self.token,
                "properties": {
                    "$os": "linux",
                    "$browser": "DiscordKeepAlive",
                    "$device": "DiscordKeepAlive"
                },
                "presence": {
                    "status": "online",
                    "activities": [{"name": "Fucking RICH 💸💸", "type": 0}],
                    "afk": False
                }
            }
        }
        ws.send(json.dumps(identify))
        
    def on_gateway_message(self, ws, message):
        try:
            data = json.loads(message)
            op = data.get('op')
            t = data.get('t')
            d = data.get('d', {})
            
            if op == 10:  # Hello
                self.heartbeat_interval = d['heartbeat_interval'] / 1000
                threading.Thread(target=self.gateway_heartbeat, daemon=True).start()
                
            elif t == 'READY':
                self.user_id = d['user']['id']
                logger.info(f"🎙️ [{self.account_name}] Ready (User: {d['user']['username']})")
                # Send voice state update to join channel
                self.join_voice(ws)
                
            elif t == 'VOICE_STATE_UPDATE':
                if d.get('user_id') == self.user_id:
                    self.session_id = d.get('session_id')
                    logger.info(f"🎙️ [{self.account_name}] Voice session ID: {self.session_id}")
                    
            elif t == 'VOICE_SERVER_UPDATE':
                self.endpoint = d.get('endpoint')
                self.voice_token = d.get('token')
                logger.info(f"🎙️ [{self.account_name}] Voice server: {self.endpoint}")
                if self.endpoint and self.voice_token and self.session_id:
                    self.connect_voice()
                    
        except Exception as e:
            logger.error(f"🎙️ [{self.account_name}] Gateway msg error: {e}")
            
    def join_voice(self, ws):
        payload = {
            "op": 4,
            "d": {
                "guild_id": self.guild_id,
                "channel_id": self.channel_id,
                "self_mute": False,
                "self_deaf": True   # Deafen - no audio sent/received, but still connected
            }
        }
        ws.send(json.dumps(payload))
        logger.info(f"🎙️ [{self.account_name}] Requested join voice channel {self.channel_id}")
        
    def connect_voice(self):
        """Connect to voice WebSocket and start UDP"""
        # Remove port from endpoint
        endpoint_host = self.endpoint.split(':')[0]
        voice_url = f"wss://{endpoint_host}:443?v=4"
        
        self.voice_ws = websocket.WebSocketApp(
            voice_url,
            on_open=self.on_voice_open,
            on_message=self.on_voice_message,
            on_error=self.on_voice_error,
            on_close=self.on_voice_close
        )
        threading.Thread(target=self.voice_ws.run_forever, daemon=True).start()
        
    def on_voice_open(self, ws):
        logger.info(f"🎙️ [{self.account_name}] Voice WebSocket opened")
        # Send identify to voice server
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
        
    def on_voice_message(self, ws, message):
        try:
            data = json.loads(message)
            op = data.get('op')
            d = data.get('d', {})
            
            if op == 2:  # Ready
                self.ssrc = d.get('ssrc')
                self.voice_ip = d.get('ip')
                self.voice_port = d.get('port')
                logger.info(f"🎙️ [{self.account_name}] Voice ready - SSRC: {self.ssrc}, IP: {self.voice_ip}, Port: {self.voice_port}")
                # Start UDP discovery
                self.udp_discovery()
                
            elif op == 4:  # Heartbeat ACK
                pass
                
            elif op == 8:  # Hello
                interval = d.get('heartbeat_interval', 41250) / 1000
                threading.Thread(target=self.voice_heartbeat, args=(ws, interval), daemon=True).start()
                
        except Exception as e:
            logger.error(f"🎙️ [{self.account_name}] Voice msg error: {e}")
            
    def udp_discovery(self):
        """Send UDP packet to discover external IP/port"""
        try:
            self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            # Packet: SSRC (4 bytes) + 70 zeros
            packet = struct.pack('>I', self.ssrc) + b'\x00' * 70
            self.udp_socket.sendto(packet, (self.voice_ip, self.voice_port))
            
            # Receive response (74 bytes)
            response, addr = self.udp_socket.recvfrom(74)
            # Extract IP and port from response
            ip = response[4:68].split(b'\x00')[0].decode()
            port = struct.unpack('>H', response[68:70])[0]
            logger.info(f"🎙️ [{self.account_name}] UDP discovery - External IP: {ip}, Port: {port}")
            
            # Send select protocol
            select_payload = {
                "op": 1,
                "d": {
                    "protocol": "udp",
                    "data": {
                        "address": ip,
                        "port": port,
                        "mode": "xsalsa20_poly1305"
                    }
                }
            }
            self.voice_ws.send(json.dumps(select_payload))
            logger.info(f"🎙️ [{self.account_name}] Voice protocol selected - UDP")
            self.connected_voice = True
            logger.info(f"✅ [{self.account_name}] SUCCESSFULLY JOINED VOICE CHANNEL (real)")
            
        except Exception as e:
            logger.error(f"🎙️ [{self.account_name}] UDP discovery error: {e}")
            
    def gateway_heartbeat(self):
        while self.running and self.gateway_ws and self.gateway_ws.sock:
            time.sleep(self.heartbeat_interval)
            try:
                self.gateway_ws.send(json.dumps({"op": 1, "d": None}))
            except:
                break
                
    def voice_heartbeat(self, ws, interval):
        while self.running and ws and ws.sock:
            time.sleep(interval)
            try:
                ws.send(json.dumps({"op": 3, "d": int(time.time() * 1000)}))
            except:
                break
                
    def on_voice_error(self, ws, error):
        logger.error(f"🎙️ [{self.account_name}] Voice WebSocket error: {error}")
        
    def on_voice_close(self, ws, code, msg):
        logger.warning(f"🎙️ [{self.account_name}] Voice closed: {code} - {msg}")
        self.reconnect()
        
    def on_gateway_error(self, ws, error):
        logger.error(f"🎙️ [{self.account_name}] Gateway error: {error}")
        
    def on_gateway_close(self, ws, code, msg):
        logger.warning(f"🎙️ [{self.account_name}] Gateway closed: {code} - {msg}")
        if self.running:
            self.reconnect()
            
    def reconnect(self):
        logger.info(f"🔄 [{self.account_name}] Reconnecting voice...")
        time.sleep(5)
        if self.udp_socket:
            self.udp_socket.close()
        self.connect_gateway()
        
    def stop(self):
        self.running = False
        if self.udp_socket:
            self.udp_socket.close()
        if self.voice_ws:
            self.voice_ws.close()
        if self.gateway_ws:
            self.gateway_ws.close()

# ============================
# MAIN DISCORD CLIENT (WITH STATUS + VOICE)
# ============================
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
        self.reconnect_attempts = 0
        
        self.voice_conn = None
        self.voice_enabled = False
        self.voice_guild_id = None
        self.voice_channel_id = None
        
    def set_voice(self, enabled, guild_id, channel_id):
        self.voice_enabled = enabled
        self.voice_guild_id = guild_id
        self.voice_channel_id = channel_id
        
    def connect(self):
        while self.running and self.reconnect_attempts < 10:
            try:
                logger.info(f"{'💰' if self.fixed_status else '🔄'} [{self.account_name}] Connecting to Discord...")
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
                
            if op == 10:  # Hello
                self.heartbeat_interval = d['heartbeat_interval']
                self.identify()
                threading.Thread(target=self.heartbeat_loop, daemon=True).start()
                if self.rotating_statuses:
                    threading.Thread(target=self.rotation_loop, daemon=True).start()
                # Status refresh thread for fixed status
                if self.fixed_status:
                    threading.Thread(target=self.status_refresh, daemon=True).start()
                    
            elif op == 11:  # Heartbeat ACK
                pass
                
            elif op == 0:  # Dispatch
                if t == 'READY':
                    self.session_id = d.get('session_id')
                    user = d.get('user', {})
                    logger.info(f"🎉 [{self.account_name}] Logged in as {user.get('username')}")
                    # Set initial status
                    if self.fixed_status:
                        self.update_status(self.fixed_status)
                    elif self.rotating_statuses:
                        self.update_status(self.rotating_statuses[0])
                    # Start voice if enabled
                    if self.voice_enabled and self.voice_channel_id:
                        self.start_voice()
                        
                elif t == 'RESUMED':
                    logger.info(f"🔄 [{self.account_name}] Session resumed")
                    # Re-apply status
                    if self.fixed_status:
                        self.update_status(self.fixed_status)
                    elif self.rotating_statuses:
                        self.update_status(self.rotating_statuses[self.current_index])
                        
            elif op == 9 or op == 7:  # Invalid session or reconnect
                logger.warning(f"⚠️ [{self.account_name}] Reconnect requested")
                time.sleep(2)
                self.resume_or_reconnect()
                
        except Exception as e:
            logger.error(f"❌ [{self.account_name}] Message error: {e}")
            
    def on_error(self, ws, error):
        logger.error(f"💥 [{self.account_name}] WebSocket error: {error}")
        
    def on_close(self, ws, code, msg):
        logger.warning(f"🔌 [{self.account_name}] Connection closed: {code}")
        if self.running:
            time.sleep(2)
            
    def update_status(self, status_text):
        """Set playing status"""
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
            if self.send_json(payload):
                logger.info(f"{'💰' if self.fixed_status else '🔄'} [{self.account_name}] Status: {status_text}")
                return True
        except Exception as e:
            logger.error(f"Status update error: {e}")
        return False
        
    def status_refresh(self):
        """Refresh fixed status every 30 minutes"""
        time.sleep(60)
        while self.running:
            time.sleep(1800)  # 30 minutes
            if self.ws and self.ws.sock and self.ws.sock.connected:
                self.update_status(self.fixed_status)
                
    def rotation_loop(self):
        """Rotate through statuses"""
        time.sleep(10)
        while self.running:
            time.sleep(self.interval_seconds)
            self.current_index = (self.current_index + 1) % len(self.rotating_statuses)
            self.update_status(self.rotating_statuses[self.current_index])
            
    def start_voice(self):
        self.voice_conn = RealVoiceConnection(
            self.token,
            self.voice_guild_id,
            self.voice_channel_id,
            self.account_name
        )
        threading.Thread(target=self.voice_conn.start, daemon=True).start()
        
    def send_json(self, data):
        try:
            if self.ws and self.ws.sock and self.ws.sock.connected:
                self.ws.send(json.dumps(data))
                return True
        except:
            pass
        return False
        
    def identify(self):
        first_status = ""
        if self.fixed_status:
            first_status = self.fixed_status
        elif self.rotating_statuses:
            first_status = self.rotating_statuses[0]
        else:
            first_status = "Online"
            
        payload = {
            "op": 2,
            "d": {
                "token": self.token,
                "properties": {"$os": "linux", "$browser": "DiscordKeepAlive", "$device": "DiscordKeepAlive"},
                "presence": {
                    "status": "online",
                    "activities": [{"name": first_status, "type": 0}],
                    "afk": False
                }
            }
        }
        self.send_json(payload)
        logger.info(f"📨 [{self.account_name}] Identify sent")
        
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
        threading.Thread(target=self.connect, daemon=True).start()
        
    def stop(self):
        self.running = False
        if self.voice_conn:
            self.voice_conn.stop()
        if self.ws:
            self.ws.close()

# ============================
# KEEP-ALIVE PINGER
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
        logger.info("📊 System running - Voice and Status active")
        time.sleep(3600)

# ============================
# MAIN
# ============================
def main():
    print("=" * 60)
    print("DISCORD KEEP-ALIVE - REAL VOICE CHANNEL JOIN")
    print("💰 Account 1: Fixed 'Fucking RICH 💸💸'")
    print("🔄 Account 2: Rotating: Working On New Video🎥 | NYT💤 | YT- NOTE YOUR TYPE")
    print("🎙️ Voice will actually appear in VC with people")
    print("=" * 60)

    token_one = os.environ.get('DISCORD_TOKEN_ONE')
    token_two = os.environ.get('DISCORD_TOKEN_TWO')
    
    # Voice settings
    voice_one_enabled = os.environ.get('VOICE_JOIN_ONE', 'false').lower() == 'true'
    voice_two_enabled = os.environ.get('VOICE_JOIN_TWO', 'false').lower() == 'true'
    
    # Your specific guild and channel IDs
    # From channel link: https://discord.com/channels/893842188037943346/896743673587437568
    GUILD_ID = "893842188037943346"
    CHANNEL_ID = "896743673587437568"
    
    # Use these IDs if no custom override
    voice_one_guild = os.environ.get('VOICE_GUILD_ID_ONE', GUILD_ID)
    voice_one_channel = os.environ.get('VOICE_CHANNEL_ID_ONE', CHANNEL_ID)
    voice_two_guild = os.environ.get('VOICE_GUILD_ID_TWO', GUILD_ID)
    voice_two_channel = os.environ.get('VOICE_CHANNEL_ID_TWO', CHANNEL_ID)
    
    rotation_interval = int(os.environ.get('ROTATION_INTERVAL_MINUTES', '30'))
    rotational_statuses = ["Working On New Video🎥", "NYT💤", "YT- NOTE YOUR TYPE"]
    
    # Start Flask and utils
    threading.Thread(target=start_flask, daemon=True).start()
    threading.Thread(target=render_pinger, daemon=True).start()
    threading.Thread(target=health_monitor, daemon=True).start()
    time.sleep(3)
    
    clients = []
    
    if token_one:
        client1 = DiscordClient(token_one, "ACCOUNT_ONE", fixed_status="Fucking RICH 💸💸")
        if voice_one_enabled:
            client1.set_voice(True, voice_one_guild, voice_one_channel)
        client1.start()
        clients.append(client1)
        logger.info(f"✅ Account One - Fixed status, Voice: {voice_one_enabled} (Guild: {voice_one_guild}, Channel: {voice_one_channel})")
        
    if token_two:
        client2 = DiscordClient(token_two, "ACCOUNT_TWO", rotating_statuses=rotational_statuses, interval_minutes=rotation_interval)
        if voice_two_enabled:
            client2.set_voice(True, voice_two_guild, voice_two_channel)
        client2.start()
        clients.append(client2)
        logger.info(f"✅ Account Two - Rotational every {rotation_interval}min, Voice: {voice_two_enabled}")
        
    logger.info("=" * 60)
    logger.info("✅ ALL SYSTEMS ONLINE - Both accounts will appear in voice channel")
    logger.info("🎙️ Voice connection uses full UDP handshake - real join")
    logger.info("=" * 60)
    
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        for c in clients:
            c.stop()
        time.sleep(2)

if __name__ == "__main__":
    main()
