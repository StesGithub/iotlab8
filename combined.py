from machine import Pin, Timer, ADC
from network import WLAN
import time
import umqtt.simple as umqtt
import machine

# ==============================
# CONFIGURATION (EDIT THESE)
# ==============================

# MQTT broker IP / hostname
BROKER_IP = '10.154.161.13'      # string or bytes

# MQTT topic name
TOPIC = b'temp/pico'             # string or bytes

# Role selection:
#   - To run as SUBSCRIBER: set OUTPUT_PIN to a pin name/number, and PUB_IDENT = None
#   - To run as PUBLISHER:  set OUTPUT_PIN = None, and PUB_IDENT to a unique ID (string or bytes)

OUTPUT_PIN = None                # e.g. "LED" or 15 for subscriber, None for publisher
PUB_IDENT = b'pico-1'            # unique ID for publisher, None for subscriber

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


# ==============================
# INTERNAL NORMALIZATION
# ==============================

# Normalize topic as bytes
if isinstance(TOPIC, str):
    TOPIC_B = TOPIC.encode()
else:
    TOPIC_B = TOPIC

# Normalize broker IP as string for MQTT
if isinstance(BROKER_IP, bytes):
    BROKER_HOST = BROKER_IP.decode()
else:
    BROKER_HOST = BROKER_IP

# Normalize publisher ID as bytes (may be None)
if isinstance(PUB_IDENT, str):
    PUB_IDENT_B = PUB_IDENT.encode()
else:
    PUB_IDENT_B = PUB_IDENT

# Validate role configuration
if (OUTPUT_PIN is None and PUB_IDENT_B is None) or (OUTPUT_PIN is not None and PUB_IDENT_B is not None):
    raise ValueError("Config error: exactly ONE of OUTPUT_PIN or PUB_IDENT must be set (not both, not neither).")

IS_PUBLISHER = OUTPUT_PIN is None


# ==============================
# WIFI
# ==============================

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
            # Connected but isconnected() might lag
            print("Wi-Fi status OK")
            return True
        time.sleep(1)
        timeout -= 1

    print("Failed to connect to Wi-Fi.")
    return False


# ==============================
# MQTT
# ==============================

def make_mqtt_client():
    # Choose a client_id:
    # - if publisher: use PUB_IDENT
    # - if subscriber: use machine.unique_id()
    if IS_PUBLISHER:
        client_id = PUB_IDENT_B
    else:
        client_id = machine.unique_id()

    mqtt = umqtt.MQTTClient(
        client_id=client_id,
        server=BROKER_HOST,
        port=PORT,
        keepalive=KEEPALIVE
    )
    return mqtt


# ==============================
# PUBLISHER: Temperature reading
# ==============================

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
        # Keep it simple – this is a lab
        print("Error during publish:", e)


# ==============================
# SUBSCRIBER: State tracking
# ==============================

# Map: publisher_id (bytes) -> (latest_temp, last_timestamp)
publisher_state = {}
output_pin = None

def setup_output_pin():
    if OUTPUT_PIN is None:
        return None
    # OUTPUT_PIN can be "LED" or a number
    return Pin(OUTPUT_PIN, Pin.OUT)

def compute_average(now=None):
    """Average temperature over publishers with data in the last TEMP_TIMEOUT_SEC."""
    if now is None:
        now = time.time()

    cutoff = now - TEMP_TIMEOUT_SEC
    total = 0.0
    count = 0

    # Use list(...) so we can safely delete while iterating
    for pub_id, (temp, ts) in list(publisher_state.items()):
        if ts < cutoff:
            # Drop stale publishers
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
    Simple behaviour: if average temp > TEMP_THRESHOLD, turn output ON; else OFF.
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


# ==============================
# MAIN
# ==============================

mqtt = None

def main():
    global mqtt, pub_timer, output_pin

    role = "PUBLISHER" if IS_PUBLISHER else "SUBSCRIBER"
    print("Starting in", role, "mode")

    # Connect Wi-Fi
    if not connect_wifi(wifi, SSID, PASSWORD):
        print("Cannot continue without Wi-Fi.")
        return

    # Setup MQTT
    mqtt = make_mqtt_client()
    mqtt.connect()
    print("Connected to MQTT broker at", BROKER_HOST)

    if IS_PUBLISHER:
        # Setup periodic publishing
        pub_timer = Timer()
        pub_timer.init(
            period=int(PUBLISH_INTERVAL_SEC * 1000),
            mode=Timer.PERIODIC,
            callback=publish_temp
        )
        # Publisher just idles; timer does the work
        while True:
            time.sleep(1)

    else:
        # Subscriber: setup output and callback, then loop
        output_pin = setup_output_pin()
        mqtt.set_callback(mqtt_callback)
        mqtt.subscribe(TOPIC_B)
        print("Subscribed to topic:", TOPIC_B)

        while True:
            # Wait for new messages and handle them with callback
            try:
                mqtt.wait_msg()
            except Exception as e:
                print("Error while waiting for MQTT message:", e)
                time.sleep(1)

# Run main
main()
