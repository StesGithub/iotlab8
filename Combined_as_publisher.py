from machine import Pin, Timer, ADC
from network import WLAN
import time
import umqtt.simple as umqtt
import machine

# ============================================================
# GLOBAL CONFIG (for the marking rubric)
# ============================================================

# BROKER_IP – IP address of the MQTT broker as string or bytes
BROKER_IP = '10.154.161.13'      # set this on BOTH publisher & subscriber

# TOPIC – MQTT topic name as string or bytes
TOPIC = b'temp/pico'             # set this on BOTH publisher & subscriber

# OUTPUT_PIN – pin name/number used for output (subscriber only)
#   - SUBSCRIBER: set to pin name/number, e.g. "LED" or 15
#   - PUBLISHER:  set to None
OUTPUT_PIN = None                # <- PUBLISHER example; change on subscriber

# PUB_IDENT – unique ID for publisher (string or bytes)
#   - PUBLISHER:  set to a unique ID, e.g. "pico-1"
#   - SUBSCRIBER: set to None
PUB_IDENT = "pico-1"             # <- PUBLISHER example; set to None on subscriber


# ============================================================
# OTHER CONFIG
# ============================================================

# Wi-Fi credentials
SSID = "blep"
PASSWORD = "12345678"

# MQTT connection parameters
PORT = 1883
KEEPALIVE = 7000

# Publisher settings
PUBLISH_INTERVAL_SEC = 2.0       # how often to send temperature

# Subscriber settings
TEMP_TIMEOUT_SEC = 10 * 60       # 10 minutes
TEMP_THRESHOLD = 25.0            # threshold for turning output ON


# ============================================================
# NORMALISATION & ROLE FLAGS
# ============================================================

# Normalize topic as bytes
if isinstance(TOPIC, str):
    TOPIC_B = TOPIC.encode()
else:
    TOPIC_B = TOPIC

# Normalize broker IP as string
if isinstance(BROKER_IP, bytes):
    BROKER_HOST = BROKER_IP.decode()
else:
    BROKER_HOST = BROKER_IP

# Normalize publisher ID as bytes (or None)
if isinstance(PUB_IDENT, str):
    PUB_IDENT_B = PUB_IDENT.encode()
else:
    PUB_IDENT_B = PUB_IDENT

# Role detection based on Lab 9 description:
#   - Publisher   => OUTPUT_PIN is None AND PUB_IDENT is not None
#   - Subscriber  => OUTPUT_PIN is not None AND PUB_IDENT is None
IS_PUBLISHER = (OUTPUT_PIN is None and PUB_IDENT is not None)
IS_SUBSCRIBER = (OUTPUT_PIN is not None and PUB_IDENT is None)


# ============================================================
# WIFI SETUP
# ============================================================

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
            # connected but isconnected() might lag
            print("Wi-Fi status OK")
            return True
        time.sleep(1)
        timeout -= 1

    print("ERROR: Failed to connect to Wi-Fi.")
    return False


# ============================================================
# MQTT SETUP
# ============================================================

def make_mqtt_client(is_publisher):
    # Publisher uses PUB_IDENT as client_id; subscriber uses unique_id
    if is_publisher:
        client_id = PUB_IDENT_B
    else:
        client_id = machine.unique_id()

    print("Creating MQTT client with client_id:", client_id)

    mqtt = umqtt.MQTTClient(
        client_id=client_id,
        server=BROKER_HOST,
        port=PORT,
        keepalive=KEEPALIVE
    )
    return mqtt


# ============================================================
# PUBLISHER: TEMPERATURE READING
# ============================================================

temp_sensor = None
pub_timer = None

if IS_PUBLISHER:
    # Built-in temperature sensor on ADC(4)
    temp_sensor = ADC(4)

def read_temp():
    """Read temperature from internal sensor (°C)."""
    value = temp_sensor.read_u16()
    voltage = value * (3.3 / 65535)
    temperature = 27 - (voltage - 0.706) / 0.001721
    return temperature

def make_payload(temp):
    """
    Payload format: b'<PUB_IDENT>,<temp>'
    Example: b'pico-1,23.47'
    """
    temp_str = "{:.2f}".format(temp).encode()
    return PUB_IDENT_B + b"," + temp_str

def publish_temp(timer):
    global mqtt
    try:
        temp = read_temp()
        payload = make_payload(temp)
        print("Publishing:", payload, "to topic:", TOPIC_B)
        mqtt.publish(TOPIC_B, payload)
    except Exception as e:
        print("Error during publish:", e)


# ============================================================
# SUBSCRIBER: STATE TRACKING & OUTPUT
# ============================================================

# Map: publisher_id (bytes) -> (latest_temp, last_timestamp)
publisher_state = {}
output_pin = None

def setup_output_pin():
    if OUTPUT_PIN is None:
        return None
    # OUTPUT_PIN can be "LED" or a pin number
    return Pin(OUTPUT_PIN, Pin.OUT)

def compute_average(now=None):
    """Average temp over publishers with data in the last TEMP_TIMEOUT_SEC.
       Also deletes stale entries (>10 minutes)."""
    if now is None:
        now = time.time()

    cutoff = now - TEMP_TIMEOUT_SEC
    total = 0.0
    count = 0

    # Use list(...) so we can safely delete while iterating
    for pub_id, (temp, ts) in list(publisher_state.items()):
        if ts < cutoff:
            # Drop stale publishers (memory-friendly)
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
    """Callback for subscriber: parse payload and update publisher_state."""
    print('Received message:', message, 'on topic:', topic)
    try:
        pub_id, temp_bytes = message.split(b",", 1)
        temp_val = float(temp_bytes)
        now = time.time()
        publisher_state[pub_id] = (temp_val, now)
        avg_temp = compute_average(now)
        apply_output(avg_temp)
    except Exception as e:
        print("Failed to parse message:", e)


# ============================================================
# MAIN
# ============================================================

mqtt = None

def main():
    global mqtt, pub_timer, output_pin

    # -------- CONFIG VALIDATION (for "fail gracefully" mark) --------

    if BROKER_IP is None or TOPIC is None:
        print("CONFIG ERROR: BROKER_IP and TOPIC must not be None.")
        return

    if IS_PUBLISHER and IS_SUBSCRIBER:
        # Should be impossible with current logic, but check anyway
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

    # -------- Wi-Fi connection --------
    if not connect_wifi(wifi, SSID, PASSWORD):
        print("FATAL: Cannot continue without Wi-Fi.")
        return

    # -------- MQTT setup --------
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

    # -------- Role-specific behaviour --------
    if IS_PUBLISHER:
        # Publisher: start periodic temperature publishing
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
        # Subscriber: setup output and callback, then process messages
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

# Run main
main()
