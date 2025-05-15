
import config

import sys
import time
import socket
import argparse
import subprocess
import numpy as np

from .hw import HW

# https://stackoverflow.com/a/28950776
def get_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0)
    try:
        # doesn't even have to be reachable
        s.connect(('10.254.254.254', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

def capture(hw, sock, channels, limit_samples):
    # swap buffers at the beginning since the current one probably overflowed
    hw.swap_buffers()

    print("capture is starting!")
    unlimited = limit_samples <= 0
    while unlimited or limit_samples > 0:
        try:
            data = hw.get_data()
        except ValueError:
            print("oops, probably overflowed")
            continue

        print(f"got {len(data)} samples")

        if limit_samples > 0:
            data = data[:limit_samples]
            limit_samples -= len(data)

        data_bytes = data[:, :channels].reshape(-1).view(np.uint8)
        while len(data_bytes) > 0: # while we have data to transmit
            sent = sock.send(data_bytes[:4096]) # send a reasonable amount
            if sent == 0: return # connection probably broken
            data_bytes = data_bytes[sent:] # discard bytes we sent

        time.sleep(0.1)

    print("desired number of samples sent")

def parse_args():
    parser = argparse.ArgumentParser(prog="server",
        description="Serve data from mic capture interface over a TCP socket.")
    parser.add_argument('-c', '--channels', type=int, metavar="N", default=config.number_of_microphones,
        help="Number of channels to send (from first N mics/channels), "
             "default all available.")
    parser.add_argument('-g', '--gain', type=int, default=config.gain_value,
        help="Gain value to multiply microphone data by, default 1.")
    parser.add_argument('-f', '--fake', action="store_true",
        help="Capture from fake microphones instead of real ones.")
    parser.add_argument('-r', '--raw', action="store_true",
        help="Send raw mic data instead of convolved output channels.")
    parser.add_argument('--port', type=int, default=2048,
        help="TCP port to listen on for connections.")
    parser.add_argument('--limit', type=float, default=0,
        help="Seconds of data to send per connection, default 0 for unlimited.")

    return parser.parse_args()

def serve():
    args = parse_args()
    host = get_ip()
    print(f"listening at IP {host} port {args.port}")

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind(("0.0.0.0", args.port))
    server_socket.listen(1)

    try:
        while True:
            (client_socket, address) = server_socket.accept()

            # Turn off recorder script temporarily while recording, when done turn it back on
            print("---- Stopping systemd mic recorder service")
            subprocess.run(["systemctl", "stop", "start-mic-recorder.service"])

            hw = HW()
            capture_frequency = hw.mic_freq_hz
            print(f"capture frequency is {capture_frequency}Hz")


            hw.set_gain(args.gain)
            hw.set_use_fake_mics(args.fake)
            hw.set_store_raw_data(args.raw)

            channels = args.channels
            max_channels = hw.num_mics if args.raw else hw.num_chans
            if channels is None:
                channels = max_channels
            if channels < 1 or channels > max_channels:
                raise ValueError(f"must be 1 <= channels <= {max_channels}")

            try:
                capture(hw, client_socket, channels, int(capture_frequency * args.limit))
                print("client said goodbye")
            except (ConnectionResetError, ConnectionAbortedError,
                    BrokenPipeError):
                print("client left rudely")
            finally:
                client_socket.close()
                print("---- Restarting systemd mic recorder service")
                subprocess.run(["systemctl", "start", "start-mic-recorder.service"])
    finally:
        server_socket.close()

def server():
    try:
        serve()
    except KeyboardInterrupt:
        print("bye")



if __name__ == "__main__":
    server()