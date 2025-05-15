# program to run in systemd

from . import config

import time
import wave
import argparse
import numpy as np
from pathlib import Path
import os

from .hw import HW

def parse_args():
    parser = argparse.ArgumentParser(prog="recorder",
        description="run recorder program for FOSSN mic line array")

def recorder():

    hw = HW()
    hw.set_use_fake_mics(False)
    hw.set_store_raw_data(True)
    hw.LED_off()
    hw.set_gain(config.gain_value)

    channels = config.number_of_microphones
    button_hold_amount = 8
    button_fpga_off_hold_amount = 48
    IDLE = True
    RECORD = True
    chunk_num = 0
    basepath = '/home/nixos'
    error_occured = False


    # Recorder Script
    print('Recorder Script is Running')
    while True:

        # IDLE STATE
        print('-' * 30)
        print('idle...')
        # Initiate LED Idle Sequence
        button_counter = 0
        button_off_counter = 0
        while IDLE:
            hw.LED_idle()
            # Check Button State / Wait for Press
            if hw.get_button_state():
                hw.button_press_indicate(button_counter)
                button_counter += 1
                if button_counter == button_hold_amount:
                    hw.button_press_indicate(button_counter)
                    IDLE = False
            else:
                button_counter = 0
                if hw.get_off_button_state():
                    button_off_counter += 1
                    if button_off_counter == button_fpga_off_hold_amount:
                        hw.LED_quick_blink()
                        # run shutdown code
                        import subprocess
                        subprocess.run(["poweroff"], check=True)
                        while True: # don't let user do anything silly
                            time.sleep(1)
                else:
                    button_off_counter = 0


            time.sleep(0.1)

        # ---------------------------------------------------
        # RECORD STATE
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
        chunk_num = 0
        IDLE = True
        RECORD = True
        hw.LED_off()
        os.sync()
        time.sleep(1)




if __name__ == "__main__":
    recorder()

