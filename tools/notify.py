#!/usr/bin/env python3
"""Send notification to Brian's phone via MQTT → Home Assistant automation."""
import sys
import paho.mqtt.publish as publish

import os
MQTT_HOST = os.environ.get("MQTT_HOST", os.environ.get("HUB_IP", ""))
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER", "")
MQTT_PASS = os.environ.get("MQTT_PASS", "")
TOPIC = "claude/notify"

def notify(message, title="Claude Code"):
    """Publish a notification message to MQTT."""
    payload = f'{{"title": "{title}", "message": "{message}"}}'
    publish.single(
        TOPIC,
        payload=payload,
        hostname=MQTT_HOST,
        port=MQTT_PORT,
        auth={"username": MQTT_USER, "password": MQTT_PASS},
    )
    print(f"Notification sent: {message}")

if __name__ == "__main__":
    msg = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Task complete"
    title = "Claude Code"
    notify(msg, title)
