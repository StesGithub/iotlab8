# IoT Assignment Week 8 
# Stephen Thompson C21394693
# Lukas Vaiciulaitis C21522836

from machine import Pin, PWM, Timer, ADC
from network import WLAN
import time, socket, cryptolib
import umqtt.simple as umqtt

# Initialise timer
timer = Timer()

#variables for mqtt 
HOSTNAME = '10.154.161.13'
PORT = 1883
TOPIC = b'temp/pico'

#create mqtt instance
mqtt = umqtt.MQTTClient(
    client_id = b'publish',
    server = HOSTNAME.encode(),
    port = PORT,
    keepalive = 7000
)


# WiFi setup
wifi = WLAN(WLAN.IF_STA)
wifi.active(True)
ssid = "blep"
password = "12345678"

# wifi connection logic
def connect(wifi_obj, ssid, password, timeout=10):
    print(f"trying to connect to wifi")
    wifi_obj.connect(ssid, password)
    if wifi_obj.isconnected():
        print(f"Connected succesfully")
    while timeout > 0:
        if wifi_obj.status() == 3:
            return True
        time.sleep(1)
        timeout -= 1
    return False

#Read temperature and publish to mqtt
def mosquitto(timer):
    global mqtt
    temp = read_temp()
    print(temp, TOPIC)
    mqtt.publish(TOPIC, str(temp).encode())
    

# temperature setup
temp_sensor = ADC(4)  # built in temp sensor


# read temp from sensor
def read_temp():
    value = temp_sensor.read_u16()
    voltage = value * (3.3 / 65535)
    temperature = 27 - (voltage - 0.706) / 0.001721
    return temperature



#try connecting to wifi and then run mqtt
try:
    is_connected = connect(wifi, ssid, password)
    if is_connected:
        mqtt.connect()
        timer.init(freq=2.0, mode=Timer.PERIODIC, callback=mosquitto)
except:
    print("no wifi")
