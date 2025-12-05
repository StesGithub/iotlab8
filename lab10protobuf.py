from machine import Pin, Timer, ADC
from network import WLAN
import time
import umqtt.simple as umqtt
import machine

from schema_upb2 import TempreadingMessage, TimeMessage as ProtoTime

# CONFIG

# broker ip
BROKER_IP = '10.154.161.13'

# topic b'temp/pico'  
TOPIC = b'temp/pico'

# output pub for subscriber
OUTPUT_PIN = None

# PUB_IDENT
PUB_IDENT = "pico-1"

# Wi-Fi config
SSID = "blep"
PASSWORD = "12345678"

# MQTT connection parameters
PORT = 1883
KEEPALIVE = 7000

# temp measure freq
PUBLISH_INTERVAL_SEC = 2.0

# timeout/threshold
TEMP_TIMEOUT_SEC = 10 * 60
TEMP_THRESHOLD = 25.0

#normalization

if isinstance(TOPIC, str):
    TOPIC_B = TOPIC.encode()
else:
    TOPIC_B = TOPIC

# normalize broker IP as string
if isinstance(BROKER_IP, bytes):
    BROKER_HOST = BROKER_IP.decode()
else:
    BROKER_HOST = BROKER_IP

# bormalize publisher ID as bytes
if isinstance(PUB_IDENT, str):
    PUB_IDENT_B = PUB_IDENT.encode()
else:
    PUB_IDENT_B = PUB_IDENT

# role detection
IS_PUBLISHER = (OUTPUT_PIN is None and PUB_IDENT is not None)
IS_SUBSCRIBER = (OUTPUT_PIN is not None and PUB_IDENT is None)


# init wifi

wifi = WLAN(WLAN.IF_STA)
wifi.active(True)

def connect_wifi(wifi_obj, ssid, password, timeout=10):
    print("Connecting to Wi-Fi...")
    wifi_obj.connect(ssid, password)

    while timeout > 0:
        if wifi_obj.isconnected():
            print("Wi-Fi connected:", wifi_obj.ifconfig())
            return True
        if wifi_obj.status() == 3:
            print("Wi-Fi status OK")
            return True
        time.sleep(1)
        timeout -= 1

    print("ERROR: Failed to connect to Wi-Fi.")
    return False


# mqtt setup

def make_mqtt_client(is_publisher):
    # Publisher uses PUB_IDENT as client_id; subscriber uses unique_id
    if is_publisher:
        client_id = PUB_IDENT_B
    else:
        client_id = "subscriber"
        
    print("Creating MQTT client with client_id:", client_id)

    mqtt = umqtt.MQTTClient(
        client_id=client_id,
        server=BROKER_HOST,
        port=PORT,
        keepalive=KEEPALIVE
    )
    return mqtt


# publisher temp reading with protobuf

temp_sensor = None
pub_timer = None

if IS_PUBLISHER:
    # built in sensor
    temp_sensor = ADC(4)

def read_temp():
    """Read temperature from internal sensor (°C)."""
    value = temp_sensor.read_u16()
    voltage = value * (3.3 / 65535)
    temperature = 27 - (voltage - 0.706) / 0.001721
    return temperature

def make_proto_payload(temp):
    """
    Build a TempreadingMessage protobuf message and serialize it.
    """
    msg = TempreadingMessage()

    # publisher_id (SET using normal attribute)
    if isinstance(PUB_IDENT_B, bytes):
        msg.publisher_id = PUB_IDENT_B.decode()
    else:
        msg.publisher_id = PUB_IDENT_B

    # temperature (SET using normal attribute)
    msg.temperature = float(temp)

    # time (hour, minute, second)
    tstruct = time.localtime()
    tmsg = ProtoTime()
    tmsg.hour = tstruct[3]
    tmsg.minute = tstruct[4]
    tmsg.second = tstruct[5]

    # assign serialized bytes for nested field
    msg.time = tmsg.serialize()

    # convert to bytes
    payload = msg.serialize()

    return payload



def publish_temp(timer):
    global mqtt
    try:
        temp = read_temp()
        payload = make_proto_payload(temp)

        # --- DEBUG LOGGING ---
        print("\n=== PROTOBUF PUBLISH DEBUG ===")
        print("Raw bytes:     ", payload)
        print("Hex:           ", payload.hex())
        print("Length:        ", len(payload))
        print("==============================")

        mqtt.publish(TOPIC_B, payload)

    except Exception as e:
        print("Error during publish:", e)



# map publisher_id to latest temp and time stamp
publisher_state = {}
output_pin = None

def setup_output_pin():
    if OUTPUT_PIN is None:
        return None
    return Pin(OUTPUT_PIN, Pin.OUT)

def compute_average(now=None):
    """Average temp over publishers with data in the last TEMP_TIMEOUT_SEC.
       Also deletes stale entries (>10 minutes)."""
    if now is None:
        now = time.time()

    cutoff = now - TEMP_TIMEOUT_SEC
    total = 0.0
    count = 0

    for pub_id, (temp, ts) in list(publisher_state.items()):
        if ts < cutoff:
            # dropping stale publishers
            print("Dropping stale publisher:", pub_id)
            del publisher_state[pub_id]
            continue
        total += temp
        count += 1

    if count == 0:
        return None
    return total / count

def apply_output(avg_temp):
    """
    If average temp > TEMP_THRESHOLD, turn output ON; else OFF.
    If no recent data, turn OFF.
    """
    if output_pin is None:
        return

    if avg_temp is None:
        print("No recent temperature readings; turning output OFF.")
        output_pin.off()
        return

    print("Average temperature:", avg_temp)
    if avg_temp > TEMP_THRESHOLD:
        print("Average above threshold; turning output ON.")
        output_pin.on()
    else:
        print("Average below threshold; turning output OFF.")
        output_pin.off()

def mqtt_callback(topic, message):
    """Callback for subscriber: parse protobuf payload and update publisher_state."""
    print('Received MQTT message ({} bytes) on topic:'.format(len(message)), topic)
    try:
        # parse the protobuf message
        msg = TempreadingMessage()
        msg.parse(message)

        # extract fields
        pub_id_str = msg.publisher_id._value
        temp_val = msg.temperature._value

        # time fields
        tmsg = msg.time._value
        hour = tmsg.hour._value
        minute = tmsg.minute._value
        second = tmsg.second._value

        print("Decoded protobuf -> publisher_id={}, temp={}, time={:02d}:{:02d}:{:02d}".format(
            pub_id_str, temp_val, hour, minute, second
        ))

        # Keep dict key as bytes for consistency
        pub_id = pub_id_str.encode() if isinstance(pub_id_str, str) else pub_id_str

        now = time.time()
        publisher_state[pub_id] = (temp_val, now)
        avg_temp = compute_average(now)
        apply_output(avg_temp)
    except Exception as e:
        print("Failed to parse protobuf message:", e)


# MAIN

mqtt = None

def main():
    global mqtt, pub_timer, output_pin

    # config validation

    if BROKER_IP is None or TOPIC is None:
        print("CONFIG ERROR: BROKER_IP and TOPIC must not be None.")
        return

    if IS_PUBLISHER and IS_SUBSCRIBER:
        print("CONFIG ERROR: Ambiguous role (both publisher and subscriber).")
        print("Exactly one of OUTPUT_PIN or PUB_IDENT must be set.")
        return

    if not IS_PUBLISHER and not IS_SUBSCRIBER:
        print("CONFIG ERROR: Invalid role configuration.")
        print("Publisher:  OUTPUT_PIN = None, PUB_IDENT != None")
        print("Subscriber: OUTPUT_PIN != None, PUB_IDENT = None")
        return

    role = "PUBLISHER" if IS_PUBLISHER else "SUBSCRIBER"
    print("Starting in", role, "mode")

    # connect to wifi
    if not connect_wifi(wifi, SSID, PASSWORD):
        print("FATAL: Cannot continue without Wi-Fi.")
        return

    # mqtt setup
    try:
        mqtt_client = make_mqtt_client(IS_PUBLISHER)
    except Exception as e:
        print("FATAL: Failed to create MQTT client:", e)
        return

    try:
        mqtt_client.connect()
        print("Connected to MQTT broker at", BROKER_HOST)
    except Exception as e:
        print("FATAL: Failed to connect to MQTT broker:", e)
        return

    mqtt = mqtt_client

    # pub/sub behavior
    if IS_PUBLISHER:
        # publisher (start periodic temp reads)
        pub_timer = Timer()
        pub_timer.init(
            period=int(PUBLISH_INTERVAL_SEC * 1000),
            mode=Timer.PERIODIC,
            callback=publish_temp
        )
        print("Publisher active; publishing every", PUBLISH_INTERVAL_SEC, "seconds.")
        while True:
            time.sleep(1)

    else:
        # subscriber: setup output and callback, then process messages
        output_pin = setup_output_pin()
        mqtt.set_callback(mqtt_callback)
        mqtt.subscribe(TOPIC_B)
        print("Subscriber active; subscribed to topic:", TOPIC_B)

        while True:
            try:
                mqtt.wait_msg()
            except Exception as e:
                print("Error while waiting for MQTT message:", e)
                time.sleep(1)

main()
