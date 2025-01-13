import os
import mmap
import time

import numpy as np

from .volatile import VolatileU32Array

class HW:
    def __init__(self):
        # open file descriptors to memory so we can map it. one is sync
        # (to access registers) and the other is not (for the cache-coherent
        # data buffer in SDRAM)
        try:
            self._buf_fd = os.open("/dev/mem", os.O_RDWR)
        except PermissionError:
            self._closed = True # prevent __del__ from running
            raise

        self._reg_fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)

        # memory map the two areas

        # 16 MiB buffer area at end of 1GiB SDRAM
        self._buf_mmap = mmap.mmap(self._buf_fd,
            0x100_0000, offset=0x3f00_0000)
        # 1KiB register area at start of FPGA lightweight slave region
        self._reg_mmap = mmap.mmap(self._reg_fd,
            0x400, offset = 0xff20_0000)

        # expose as numpy arrays

        # expose as two regions of signed 16 bit words
        self.d = np.frombuffer(self._buf_mmap, dtype=np.int16).reshape(2, -1)
        # expose uint32 register data through volatile pointer
        self.r = VolatileU32Array(memoryview(self._reg_mmap).cast('L'))

        self._closed = False

        # access test register to make sure the bus seems alive
        val = self.r[0]
        val = ((val + 0x1234) * 3) & 0xFFFF_FFFF # permute the value somehow
        self.r[0] = val
        if self.r[0] != val:
            raise ValueError("test register not responding")

        # read system parameters
        p1 = self.r[8]
        p2 = self.r[9]
        self.num_mics = p1 & 0xFF
        self.num_chans = (p1 >> 8) & 0xFF
        self.num_taps = (p1 >> 16) & 0xFF
        self.mic_freq_hz = p2 & 0xFFFF

        self._store_raw_data = bool(self.r[10]) # need to know for data shape

        # wait for any existing buffer swap to have completed
        while self.r[2] & 1: pass

        self.idle_num = 0
        self.previous_idle_num = -1

        self.rec_blink_value = 0
        self.rec_blink_state = True

    def swap_buffers(self):
        # swap buffers and return (old buffer, old address)

        # ask for buffers to be swapped
        self.r[2] = 1
        # loop until it occurs (at about 48KHz so no point sleeping)
        while (status := self.r[2]) & 1: pass

        which = (status >> 1) & 1 # which buffer did we swap from?
        where = self.r[3] # what was the last address in that buffer?
        return (which, where)

    def get_data(self):
        # swap buffers then return a reference to the buffered data
        which_buf, buf_pos = self.swap_buffers()
        buf_pos >>= 1 # convert from bytes to words

        dim = self.num_mics if self._store_raw_data else self.num_chans
        return self.d[which_buf, :buf_pos].reshape(-1, dim)

    def set_gain(self, gain):
        # set the value to multiply the microphone data by (i.e. gain)

        gain = int(gain)
        if gain < 1 or gain > 256:
            raise ValueError("must be 1 <= gain <= 256")

        self.r[4] = gain - 1

    def set_use_fake_mics(self, use_fake_mics=True):
        # set whether fake mics should be used or not

        self.r[5] = 1 if use_fake_mics else 0

    def set_store_raw_data(self, store_raw_data=True, wait=True):
        # set whether to store raw data or not

        self._store_raw_data = bool(store_raw_data)
        self.r[10] = int(self._store_raw_data)

        if wait:
            # wait enough time for the switch to happen and data to be processed
            # so everything is current, then discard the in-between stuff
            time.sleep((1/self.mic_freq_hz) * (self.num_taps + 10))
            self.swap_buffers()

    def get_button_state(self):
        p1 = self.r[11]
        button_value = p1 & 0x1

        if button_value == 1: button_state = True
        else: button_state = False

        return button_state

    def get_off_button_state(self):
        p1 = self.r[11]
        return bool(p1 & (1 << 13))

    def get_gain(self):
        p1 = self.r[11]
        switch_value = (p1 >> 1) & 0xF
        multiplier = 3
        return (switch_value * multiplier) + 1

    def LED_off(self):
        self.r[11] &= ~(0xFF << 5)

    def LED_on(self):
        self.r[11] |= (0xFF << 5)

    def LED_idle(self):
        self.LED_off()
        values_list = [0x80, 0x80, 0x40, 0x40, 0x20, 0x20, 0x10, 0x10,
                       0x08, 0x08, 0x04, 0x04, 0x02, 0x02, 0x01, 0x01]
        self.r[11] |= (values_list[self.idle_num] << 5)
        if self.idle_num > self.previous_idle_num:
            self.previous_idle_num = self.idle_num
            self.idle_num += 1

            if self.idle_num == 16:
                self.idle_num = 14

        else:
            self.previous_idle_num = self.idle_num
            self.idle_num -= 1

            if self.idle_num == -1:
                self.idle_num = 1

    def button_press_indicate(self, number):
        self.LED_off()
        values_list = [0x00, 0x80, 0xC0, 0xE0, 0xF0, 0xF8, 0xFC, 0xFE, 0xFF]
        self.r[11] |= (values_list[number] << 5)

    def button_press_indicate_r(self, number):
        self.LED_off()
        values_list = [0xFF, 0xFE, 0xFC, 0xF8, 0xF0, 0xE0, 0xC0, 0x80, 0x00]
        self.r[11] |= (values_list[number] << 5)

    def LED_recording(self):
        if self.get_button_state():
            pass
        else:
            if self.rec_blink_value % 5 == 0:
                if self.rec_blink_state:
                    self.LED_on()
                    self.rec_blink_state = False
                else:
                    self.LED_off()
                    self.rec_blink_state = True

            self.rec_blink_value += 1
            if self.rec_blink_value == 100:
                self.rec_blink_value = 0

    def LED_quick_blink(self):
        num_blinks = 5
        delaytime = 0.1
        self.LED_off()
        for i in range(num_blinks):
            self.LED_on()
            time.sleep(delaytime)
            self.LED_off()
            time.sleep(delaytime)

    def close(self):
        if self._closed:
            raise ValueError

        self.d = None
        self.r = None

        self._buf_mmap.close()
        self._buf_mmap = None
        self._reg_mmap.close()
        self._reg_mmap = None

        os.close(self._buf_fd)
        self._buf_fd = None
        os.close(self._reg_fd)
        self._reg_fd = None

        self._closed = True

    def __del__(self):
        if not self._closed:
            self.close()
