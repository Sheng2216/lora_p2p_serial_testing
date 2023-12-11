import serial
import threading
import time
import re
from queue import Queue
import logging

# Set up logging
logging.basicConfig(format='%(asctime)s: %(message)s', level=logging.INFO)


def setup_serial(device_path, baudrate=115200):
    try:
        ser = serial.Serial(device_path, baudrate)
        time.sleep(1)  # Give the connection a second to settle
        return ser
    except Exception  as e:
        logging.error(f"Failed to setup serial connection on {device_path}: {e}")
        return None


def pre_config(device):
    logging.info(f'[Config] Setting up {device.port}...')
    setup_cmds = [
        'AT+PRECV=0',  # enter tx mode
        'AT+P2P=923000000:7:0:0:8:14',  # set P2P parameters with just one line
        'AT+P2P=?'  # check the P2P parameters
    ]

    correct_responses = ['OK', 'OK', 'AT+P2P=923000000:7:0:0:8:14']

    for i in range(len(setup_cmds)):
        cmd = setup_cmds[i]
        correct_response = correct_responses[i]
        while True:
            device.write((cmd + '\r\n').encode())  # Write command directly to the serial port
            response = device.readline().decode().strip()
            if response == correct_response:
                logging.info(f'[Config] Configuration "{cmd}" for device at "{device.port}" is done, continue...')
                break  # Exit the loop and proceed to the next command if the response was correct
            logging.warning(f'[Config] Unexpected response "{response}" to command "{cmd}", retrying...')


def send_command(device, command, event):
    try:
        device.write((command + '\r\n').encode())
        event.wait()  # Wait for event to be set
        event.clear()  # Reset the event
    except Exception as e:
        logging.error(f"Unexpected error occurred while writing to {device.port}: {e}")


def listen(device, event, queue):
    while True:
        if device.in_waiting:
            line = device.readline().decode().strip()
            logging.info(f'[AT command result] Received from {device.port}: {line}')
            if line.startswith('+EVT:RXP2P:'):
                match = re.match(r'\+EVT:RXP2P:(-?\d+):(\d+):(\w+)', line)
                if match:
                    rssi, snr, payload = match.groups()
                    logging.info(
                        f'[Test Result] LoRa p2p packet received: RSSI: {rssi}, SNR: {snr}, Payload: {payload}')
                    queue.put(payload)  # Put the payload in the queue
            event.set()  # Unblocks the send_command function


device_1 = setup_serial('/dev/ttyACM0')
# the serial port might get changed if we unplug the lora testing device when
# the RaspberryPi is on and then plug it back again, make sure to double check
# the port with command: ls /dev/tty*
event_1 = threading.Event()
device_2 = setup_serial('/dev/ttyACM1')
event_2 = threading.Event()
# pre-configure the settings on the two LoRa devices to make sure they use the following parameters:
# Frequency:                   923000000
# Spreading Factor:        7
# Bandwidth:                   125kHz
# Code Rate:                    4/5
# Preamble Length:         8
# TX Power:                      14 dBm
pre_config(device_1)
pre_config(device_2)

# Create a queue to store received payloads
payload_queue = Queue()

# Start threads to listen for responses from the devices
listen_thread_1 = threading.Thread(target=listen, args=(device_1, event_1, payload_queue), daemon=True)
listen_thread_1.start()

listen_thread_2 = threading.Thread(target=listen, args=(device_2, event_2, payload_queue), daemon=True)
listen_thread_2.start()

devices = [(device_1, event_1), (device_2, event_2)]

logging.info(f'******************************************************************************************')
logging.info(f'[Info] Test begin........')
# Run tests twice, switching device roles each time
for i in range(2):
    # Switch device roles
    devices.reverse()

    # Unpack devices and events
    (device_rx, event_rx), (device_tx, event_tx) = devices

    # Send commands to the receiver
    logging.info(f'[Config] Set {device_rx.port} to enter RX mode...')
    send_command(device_rx, 'AT+PRECV=65534',
                 event_rx)  # enter RX mode, continuously listen to P2P LoRa packets without any timeout

    # Send commands to the transmitter
    logging.info(f'[Config] Set {device_tx.port} to enter TX mode...')
    send_command(device_tx, 'AT+PRECV=0', event_tx)  # enter tx mode

    # Starting testing
    # Initialize counters
    num_successes = 0
    num_failures = 0
    test_payload = '11223344556677889900'

    for text_index in range(5):
        payload_sent = str(int(test_payload) + text_index)  # incremental payload
        send_command(device_tx, 'AT+PSEND=' + payload_sent, event_tx)

        # Wait for the response before continuing
        while payload_queue.empty():
            time.sleep(1)  # Adjust this delay as needed

        # Now check the received payload
        payload_received = payload_queue.get()  # Get the payload from the queue
        if payload_received == payload_sent:
            logging.info(f'[Test Result] Payload check succeeded: {payload_sent} was correctly received.')
            num_successes += 1  # Increment the number of successes
        else:
            logging.warning(
                f'[Test Result] Payload check failed: Sent {payload_sent}, but received {payload_received}.')
            num_failures += 1  # Increment the number of failures

    # Print the number of successful and failed tests
    print(f'[Test Result] Number of successful tests: {num_successes}')
    print(f'[Test Result] Number of failed tests: {num_failures}')
