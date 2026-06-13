import os
import re
import json
import time
import base64
import socketio
import requests
import threading
from collections import deque
from dotenv import load_dotenv
from google import genai
from flask import Flask

# Load environment variables
load_dotenv()

# --------------------------
# CONFIG
# --------------------------
USERNAME = os.getenv("MIG66_USERNAME")
PASSWORD = os.getenv("MIG66_PASSWORD")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
API_BASE = os.getenv("API_BASE")

ROOM_ID = int(os.getenv("ROOM_ID", "50"))
TRIGGER = os.getenv("TRIGGER_KEYWORD", f"@{USERNAME}").lower()
PARENT_USERS = set(x.strip().lower() for x in os.getenv("PARENT_USERS", "faysal").split(","))

# Initialize clients
client = genai.Client(api_key=GEMINI_API_KEY)
sio = socketio.Client()

# Global state
token = None
my_user_id = None
joined_rooms = set()
processed = deque(maxlen=500)
voucher_enabled = True
auto_reply_enabled = True


# --------------------------
# LOGIN
# --------------------------
def login():
    """Log in to the API and set the global token and user ID."""
    global token, my_user_id
    print("[*] Logging in...")

    resp = requests.post(
        f"{API_BASE}/api/auth/login",
        json={
            "username": USERNAME,
            "password": PASSWORD,
            "remember_me": True,
            "login_offline": False,
            "device_info": "Flutter Web",
        },
    )
    data = resp.json()
    token = data.get("token") or data.get("data", {}).get("token")

    if not token:
        raise Exception("Login failed: No token received")

    # Decode JWT payload to get user ID
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    decoded = json.loads(base64.b64decode(payload))
    my_user_id = str(decoded.get("id"))
    print(f"[✓] Logged in as {decoded.get('username')}")


# --------------------------
# AI
# --------------------------
def ask_ai(question: str) -> str:
    """Ask the AI model a question and return the response."""
    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=question,
        )
        return response.text.strip()
    except Exception as e:
        print(f"[!] AI Error: {e}")
        return "Sorry, I couldn't answer right now."


# --------------------------
# HELPERS
# --------------------------
def send_room(room_id: int, text: str) -> None:
    """Send a message to a room."""
    sio.emit(
        "send_message",
        {
            "room_id": room_id,
            "content": text,
            "msg_type": "text",
        },
    )


def send_private(username: str, text: str) -> None:
    """Send a private message to a user."""
    sio.emit(
        "private_message",
        {
            "to_username": username,
            "content": text,
        },
    )


def join_room(room_id: int) -> None:
    """Join a room."""
    room_id = int(room_id)
    sio.emit(
        "join_room",
        {
            "room_id": room_id,
            "is_manual": True,
        },
    )
    joined_rooms.add(room_id)


def leave_room(room_id: int) -> None:
    """Leave a room."""
    room_id = int(room_id)
    sio.emit(
        "leave_room",
        {
            "room_id": room_id,
        },
    )
    joined_rooms.discard(room_id)


# --------------------------
# PARENT COMMANDS
# --------------------------
def handle_parent_command(sender: str, text: str) -> None:
    """Handle commands from parent users."""
    global voucher_enabled, auto_reply_enabled

    text = text.lower()

    # Auto-reply commands
    if text in ("|ar on", "|ar on"):
        auto_reply_enabled = True
        send_private(sender, "Auto Reply ON")
        return
    elif text in ("|ar off", "|ar off"):
        auto_reply_enabled = False
        send_private(sender, "Auto Reply OFF")
        return

    # Help command
    elif text.startswith(("|help", "|h")):
        help_msg = """
Available Commands:
|jr 50        (or |join_room 50)
|lr 50        (or |leave_room 50)
|lr all       (or |leave_room all)
|tr 50 hello  (or |text_room 50 hello)
|voucher on   (or |v on)
|voucher off  (or |v off)
|ar on
|ar off
|status
"""
        send_private(sender, help_msg)
        return

    # Join room: |jr 50 or |join_room 50
    elif (m := re.match(r"\|(jr|join_room)\s+(\d+)", text)):
        room = int(m.group(2))
        join_room(room)
        send_private(sender, f"Joined room {room}")
        return

    # Leave room all: |lr all or |leave_room all
    elif text in ("|lr all", "|leave_room all"):
        for r in list(joined_rooms):
            leave_room(r)
        send_private(sender, "All rooms left")
        return

    # Leave room: |lr 50 or |leave_room 50
    elif (m := re.match(r"\|(lr|leave_room)\s+(\d+)", text)):
        room = int(m.group(2))
        leave_room(room)
        send_private(sender, f"Left room {room}")
        return

    # Text room: |tr 50 hello or |text_room 50 hello
    elif (m := re.match(r"\|(tr|text_room)\s+(\d+)\s+(.+)", text)):
        room = int(m.group(2))
        msg = m.group(3)
        send_room(room, msg)
        send_private(sender, f"Message sent to room {room}")
        return

    # Voucher commands
    elif text in ("|voucher on", "|v on"):
        voucher_enabled = True
        send_private(sender, "Voucher ON")
        return
    elif text in ("|voucher off", "|v off"):
        voucher_enabled = False
        send_private(sender, "Voucher OFF")
        return

    # Status command
    elif text == "|status":
        status_msg = f"""
Rooms: {list(joined_rooms)}
Voucher: {voucher_enabled}
AutoReply: {auto_reply_enabled}
Parents: {list(PARENT_USERS)}
"""
        send_private(sender, status_msg)
        return


# --------------------------
# SOCKET EVENTS
# --------------------------
@sio.event
def connect():
    print("[✓] Connected")
    join_room(ROOM_ID)


@sio.event
def disconnect():
    print("[!] Disconnected")


@sio.on("*")
def catch_all(event, data):
    print(f"\nEVENT: {event}")
    print(json.dumps(data, indent=2)[:500])


# --------------------------
# PRIVATE MESSAGE HANDLER
# --------------------------
@sio.on("private_message")
def private_message(data):
    sender = str(data.get("sender_name", ""))
    sender_id = str(data.get("sender_id", ""))
    text = str(data.get("content", "")).strip()

    if not text or sender_id == my_user_id:
        return

    if sender.lower() in PARENT_USERS and text.startswith("|"):
        handle_parent_command(sender, text)
        return

    if auto_reply_enabled:
        answer = ask_ai(text)
        send_private(sender, answer)


# --------------------------
# ROOM MESSAGE HANDLER
# --------------------------
@sio.on("new_message")
def room_message(data):
    sender = str(data.get("username", ""))
    sender_id = str(data.get("sender_id", ""))
    text = str(data.get("content", ""))
    room_id = data.get("room_id")

    if sender_id == my_user_id:
        return

    # Handle voucher codes
    if voucher_enabled:
        match = re.search(r"\[code\]\s+(\d+)", text, re.I)
        if match:
            code = match.group(1)
            send_room(room_id, f"/pick {code}")
            return

    # Handle AI replies
    if TRIGGER not in text.lower():
        return

    question = text.replace(TRIGGER, "").strip()
    key = f"{sender}:{question}"

    if key in processed:
        return

    processed.append(key)

    if auto_reply_enabled:
        answer = ask_ai(question)
        send_room(room_id, f"@{sender} {answer}")


# --------------------------
# MAIN
# --------------------------
def main():
    login()
    sio.connect(
        API_BASE,
        auth={"token": token},
        transports=["websocket", "polling"],
    )
    sio.wait()


# Flask app for Render
app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is running!"


def start_bot():
    main()


if __name__ == "__main__":
    # Run bot in background
    threading.Thread(target=start_bot, daemon=True).start()
    # Start web server
    port = int(os.environ.get("PORT", 10000))
    print(f"Server running on port {port}")
    app.run(host="0.0.0.0", port=port)
