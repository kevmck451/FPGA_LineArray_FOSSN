# program to run in systemd

import config

import time
import wave
import argparse
import numpy as np
from pathlib import Path
import os
import threading
import sys
import subprocess


from .hw import HW


RECORD = True  # global

def parse_args():
    parser = argparse.ArgumentParser(prog="record",
        description="record audio remotely")

def input_watcher():
    global RECORD
    print('PRESS s AND HIT ENTER TO STOP RECORDING')
    for line in sys.stdin:
        if line.strip().lower() == 's':
            print("---- Received stop command from user input.")
            RECORD = False
            break

def record():

    global RECORD
    hw = HW()
    hw.set_use_fake_mics(False)
    hw.set_store_raw_data(True)
    channels = config.number_of_microphones
    button_hold_amount = 8
    chunk_num = 0
    basepath = '/home/nixos'
    hw.LED_off()
    error_occured = False
    hw.set_gain(config.gain_value)

    # start stop watching thread
    threading.Thread(target=input_watcher, daemon=True).start()

    # Record Script
    print('recording setup initiated...')
    hw.LED_quick_blink()
    time.sleep(1)

    # ---------------------------------------------------
    # Filename Logic
    latest_num = -1
    for path in Path(basepath).rglob('*.wav'):
        prefix = int(path.stem.split('_')[0])
        latest_num = max(latest_num, prefix)
    if latest_num == -1:
        file_index = 0
    else:
        file_index = latest_num + 1

    filename = f'{file_index}_{chunk_num}'
    filepath = f'{basepath}/{filename}.wav'
    print(f'---- File Name: {filename}')

    # ---------------------------------------------------
    # create wav file to save
    wav = wave.open(filepath, "wb")
    wav.setnchannels(channels)
    wav.setsampwidth(2)
    wav.setframerate(hw.mic_freq_hz)

    # ---------------------------------------------------
    # monitor file size
    total_filesize = 0
    filesize = 0
    # swap buffers at the beginning since the current one probably overflowed
    hw.swap_buffers()

    print('---- *** RECORDING IN PROGRESS ***')
    button_counter = 0
    while RECORD:
        # Initial LED Recording Sequence
        hw.LED_recording()
        try:
            data = hw.get_data()

        # record any errors to an error log file
        except ValueError:
            print('---- oops, probably overflowed')
            error_occured = True
            continue

        # print(f'---- Samples: {len(data)}')
        filesize += (len(data) * channels * 16) / (8 * 1000 * 1000) # Mega Bytes
        wav.writeframesraw(np.ascontiguousarray(data[:, :channels]))

        # Check Button State / Wait for Press
        if hw.get_button_state():
            hw.button_press_indicate_r(button_counter)
            button_counter += 1
            if button_counter == button_hold_amount:
                hw.button_press_indicate_r(button_counter)
                RECORD = False
        else: button_counter = 0

        if filesize >= 4000:
            print('---- File Size Limit Reached')
            wav.close()
            chunk_num += 1
            filename = f'{file_index}_{chunk_num}'
            filepath = f'{basepath}/{filename}.wav'
            wav = wave.open(filepath, "wb")
            wav.setnchannels(channels)
            wav.setsampwidth(2)
            wav.setframerate(hw.mic_freq_hz)
            print(f'---- New Chunk Created: {filename}')
            total_filesize += filesize
            filesize = 0

        time.sleep(0.1)

    # ---------------------------------------------------
    # End Recording
    wav.close()
    print('---- Recording Successful')
    # LED Flashing Action for Confirmation
    hw.LED_quick_blink()
    print(f'---- File Size: {filesize:.2f} MB')

    # ---------------------------------------------------
    # create a metadata file with info about recording
    if total_filesize == 0: total_filesize = filesize
    metadata = {
        'filename' : f'{filename}.wav',
        'gain' : f'{hw.get_gain()} / 255',
        'filesize' : f'{total_filesize:.2f} MB',
        'error occured' : error_occured,
        'number of chunks' : (chunk_num+1),
        'channels' : channels,
        'bit depth' : 16,
        'sample rate' : f'{hw.mic_freq_hz} Hz',
        'temperature' : 'none',
        }

    with open(f'{basepath}/{file_index}.txt', "w") as file:
        for label, value in metadata.items():
            file.write(f"{label}: {value}\n")

    # ---------------------------------------------------
    # Reset all Flags to start loop over
    file_index += 1
    hw.LED_off()
    os.sync()
    time.sleep(1)


def main_wrapper():
    # stop it
    # Turn off recorder script temporarily while recording, when done turn it back on
    print("---- Stopping systemd mic recorder service")
    subprocess.run(["systemctl", "stop", "start-mic-recorder.service"])
    try:
        record()
    finally:
        print("---- Restarting systemd mic recorder service")
        subprocess.run(["systemctl", "start", "start-mic-recorder.service"])
        # start it


if __name__ == "__main__":
    main_wrapper()

