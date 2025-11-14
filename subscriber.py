from machine import Pin, PWM, Timer, ADC
from network import WLAN
import time, socket, cryptolib
import umqtt.simple as umqtt


#Define LED instance for on board LED
led = Pin("LED", Pin.OUT)


#Wifi setup
wifi = WLAN(WLAN.IF_STA)
wifi.active(True)
ssid = "blep"
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


#mqtt setup
HOSTNAME = '10.154.161.13'
PORT = 1883
TOPIC = b'temp/pico'

#mqtt client
mqtt = umqtt.MQTTClient(
    client_id = b'subscriber',
    server = HOSTNAME.encode(),
    port = PORT,
    keepalive = 7000
)

#callback function for when message is received
def callback(topic, message):
    print(f"I received the message \"{message}\" for topic \"{topic}\"")
    
    #ensure message is a float 
    temp_check = float(message)

    # if temperature is too high turn on the led
    if temp_check > 25.0:
        print("temp too high — turning LED ON")
        led.on()
    else:
        led.off()

    

#returns bool 
is_connected = connect(wifi, ssid, password)

# allows mqtt to connect if wifi is connected
if is_connected:
    mqtt.connect()

    mqtt.set_callback(callback)
    mqtt.subscribe(TOPIC)
    while True:
        mqtt.wait_msg()

