import time
import random
import paho.mqtt.client as mqtt

BROKER = "test.mosquitto.org"
PORT = 1883
TOPIC = "example/sensors/temperature"

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.connect(BROKER, PORT, keepalive=60)
client.loop_start()  # runs network loop in background thread

try:
    while True:
        value = round(random.uniform(18.0, 25.0), 2)
        client.publish(TOPIC, payload=str(value), qos=0)
        print(f"Published {value} to {TOPIC}")
        time.sleep(2)
except KeyboardInterrupt:
    client.loop_stop()
    client.disconnect()