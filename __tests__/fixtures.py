"""Synthetic images, devices, and processes shared by component and integration tests."""

import binascii
import io
import queue
import struct
import subprocess
import threading
import time
import zlib


A = "A" * 27 + "="
B = "B" * 27 + "="
C = "C" * 27 + "="
FIRST = {"udid": "first-phone", "name": "First", "product": "iPhone16,1", "version": "27", "connected": True}
SECOND = {"udid": "second-phone", "name": "Second", "product": "iPhone16,1", "version": "27", "connected": True}


def png_image(width, height, color):
    def chunk(kind, content):
        return (struct.pack(">I", len(content)) + kind + content
                + struct.pack(">I", binascii.crc32(kind + content) & 0xffffffff))
    rows = b"".join(b"\0" + b"".join(bytes(color(x, y)) for x in range(width))
                    for y in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Timed out waiting for asynchronous controller state")


class LiveStream:
    def __init__(self):
        self.lines = queue.Queue()

    def __iter__(self):
        return self

    def __next__(self):
        item = self.lines.get(timeout=5)
        if item is None:
            raise StopIteration
        return item

    def close(self):
        pass


class Process:
    def __init__(self, lines=None, returncode=0):
        self.stdout = LiveStream() if lines is None else io.StringIO(lines)
        self.returncode = None if lines is None else returncode
        self.finished = threading.Event()
        if lines is not None:
            self.finished.set()
        self.terminated = False

    def feed(self, text):
        self.stdout.lines.put(text)

    def end(self, status=0):
        self.returncode = status
        self.stdout.lines.put(None)
        self.finished.set()

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.end(-15)

    kill = terminate

    def wait(self, timeout=None):
        if not self.finished.wait(timeout):
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.returncode
