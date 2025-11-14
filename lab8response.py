from machine import Pin, PWM, Timer, ADC
from network import WLAN
import time, socket, cryptolib
import umqtt.simple as umqtt



#Wifi setup
wifi = WLAN(WLAN.IF_STA)
wifi.active(True)
ssid = "StepheniPhone"
password = "12345678"

#Connect to wifi
def connect(wifi_obj, ssid, password, timeout=10):
    print(wifi_obj)
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


HOSTNAME = '172.20.10.10'
PORT = 1883
TOPIC = 'temp/pico'

mqtt = umqtt.MQTTClient(
    client_id = b'subscriber',
    server = HOSTNAME.encode(),
    port = PORT,
    keepalive = 7000
)

def callback(topic, message):
    
    print(f'I received the message "{message}" for topic "{topic}"')
    if float(message) > 25:
        print("this is where we blink")
    
    
    
try:
    is_connected = connect(wifi, ssid, password)
    if is_connected:
        mqtt.connect()
        mqtt.set_callback(callback)
        mqtt.subscribe(TOPIC)
        while True:
            mqtt.wait_msg()
except:
    print("no wifi")

    

