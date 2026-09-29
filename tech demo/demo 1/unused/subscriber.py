import time
import random
import paho.mqtt.client as mqtt

BROKER = "test.mosquitto.org" #shared public broker between publisher and subscriber
#A broker is the central server that MQTT clients connect to — it's the middleman that routes messages between publishers and subscribers. Publishers and subscribers never talk to each other directly; they both connect to the broker, and the broker forwards messages based on topic.
#We should create our own broker later.
PORT = 1883
TOPIC = "example/sensors/temperature"

def on_connect(client, userdata, flags, reason_code, properties):
    print(f"Connected with result code {reason_code}")
    client.subscribe(TOPIC)

#client	the same mqtt.Client object  — passed back to you so you can call methods on it (like .subscribe(...)) from inside the callback
#userdata	arbitrary data you can attach via client.user_data_set(...) — unused here, so it's just None
#flags	a dict of connection info the broker sends back (e.g. whether a previous session was resumed)
#reason_code	the connection result from the broker (0 = success, other values = various failures) — this is why it's printed
#properties	MQTT v5 protocol metadata from the broker — mostly unused for basic use

def on_message(client, userdata, msg):
    print(f"{msg.topic}: {msg.payload.decode()}")


client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.on_connect = on_connect
client.on_message = on_message

client.connect(BROKER, PORT, keepalive=60)
client.loop_forever()  # blocks, keeps listening