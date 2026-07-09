#!/usr/bin/env python3
"""
Integration tests for DisplayBridge protocol, QR round-trip, and receiver logic.
Run: python3 tests/test_protocol.py
"""

import base64
import hashlib
import importlib.util
import os
import re
import sys
import tempfile
import unittest

import numpy as np
from pyzbar.pyzbar import decode
from qrcode import QRCode, constants

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "python"))

from displayBridgeSender import _worker_generate_qr, MAX_FILE_SIZE_BYTES  # noqa: E402


def load_receiver_module():
    spec = importlib.util.spec_from_file_location(
        "displayBridgeReceiver",
        os.path.join(REPO_ROOT, "python", "displayBridgeReceiver.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ReceiverApp = load_receiver_module().ReceiverApp


class FakeRoot:
    def after(self, *_args, **_kwargs):
        pass

    def update_idletasks(self):
        pass

    def update(self):
        pass


class ReceiverHarness:
    """Minimal harness around ReceiverApp logic without Tk GUI."""

    def __init__(self):
        self.app = ReceiverApp.__new__(ReceiverApp)
        self.app.root = FakeRoot()
        self.app.MAX_FILE_SIZE_MB = 200
        self.app.MAX_CHUNKS = 10000
        self.app.ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.pdf', '.txt', '.zip', '.webp'}
        self.app.filename = ""
        self.app.total_chunks = 0
        self.app.received_chunks = {}
        self.app.pre_start_buffer = {}
        self.app.pre_start_logged = False
        self.app.is_collecting = False
        self.app.last_success = False
        self.app.expected_hash = ""
        self.app.notify_var = type("N", (), {"set": lambda *_a, **_k: None})()
        self.app.status_var = type("S", (), {"set": lambda *_a, **_k: None})()
        self.app.progress_var = type("P", (), {"set": lambda *_a, **_k: None})()
        self.app.progress_bar = type("B", (), {
            "value": 0,
            "maximum": 0,
            "__setitem__": lambda self, k, v: setattr(self, k, v),
            "__getitem__": lambda self, k: getattr(self, k),
            "config": lambda *args, **kwargs: None,
        })()
        self.app.log_messages = []
        self.app.log_message = lambda msg: self.app.log_messages.append(msg)
        self.saved_bytes = None
        self.app.save_and_finish = self._capture_save

    def _capture_save(self):
        orig = ReceiverApp.save_and_finish
        try:
            full_b64 = "".join(self.app.received_chunks[i] for i in range(self.app.total_chunks))
            file_bytes = base64.b64decode(full_b64)
            actual_hash = hashlib.sha256(file_bytes).hexdigest()
            if actual_hash != self.app.expected_hash:
                self.app.log_message("INTEGRITY FAILURE")
                ReceiverApp.reset_ui_state(self.app)
                return
            self.saved_bytes = file_bytes
            self.app.last_success = True
            self.app.is_collecting = False
            self.app.received_chunks = {}
            self.app.pre_start_buffer = {}
            self.app.pre_start_logged = False
        except Exception as exc:
            self.app.log_message(f"Save Error: {exc}")

    def feed(self, packet):
        self.app.process_qr_data(packet)

    def save_result(self):
        if self.saved_bytes is not None:
            return self.saved_bytes
        full_b64 = "".join(self.app.received_chunks[i] for i in range(self.app.total_chunks))
        return base64.b64decode(full_b64)


def build_packets(file_path, chunk_size=480, filename_override=None):
    with open(file_path, "rb") as handle:
        content = handle.read()

    file_hash = hashlib.sha256(content).hexdigest()
    b64_str = base64.b64encode(content).decode("utf-8")
    parts = [b64_str[i:i + chunk_size] for i in range(0, len(b64_str), chunk_size)]
    filename = filename_override or os.path.basename(file_path)
    fn_b64 = base64.b64encode(filename.encode("utf-8")).decode("ascii")
    packets = [f"START|{fn_b64}|{len(parts)}|{file_hash}"]
    packets.extend(f"DATA|{index}|{chunk}" for index, chunk in enumerate(parts))
    return packets, content, file_hash, filename


def qr_roundtrip(packet):
    image = _worker_generate_qr(packet)
    frame = np.array(image.convert("RGB"))
    frame = frame[:, :, ::-1]  # RGB -> BGR for OpenCV-style array
    decoded = decode(frame)
    if not decoded:
        return None
    return decoded[0].data.decode("utf-8")


class ProtocolTests(unittest.TestCase):
    test_file = os.path.join(REPO_ROOT, "tests", "test_10kb.txt")

    def test_build_packets_and_hash(self):
        packets, content, file_hash, _filename = build_packets(self.test_file)
        self.assertTrue(packets[0].startswith("START|"))
        self.assertGreater(len(packets), 1)
        self.assertEqual(hashlib.sha256(content).hexdigest(), file_hash)

    def test_qr_roundtrip_all_packets(self):
        packets, _content, _file_hash, _filename = build_packets(self.test_file, chunk_size=200)
        for index, packet in enumerate(packets):
            decoded = qr_roundtrip(packet)
            self.assertIsNotNone(decoded, f"Packet {index} could not be decoded")
            self.assertEqual(decoded, packet)

    def test_full_transfer_via_receiver_logic(self):
        packets, original, file_hash, filename = build_packets(self.test_file, chunk_size=300)
        receiver = ReceiverHarness()

        for packet in packets:
            receiver.feed(packet)

        self.assertTrue(receiver.app.last_success)
        reconstructed = receiver.save_result()
        self.assertEqual(original, reconstructed)
        self.assertEqual(filename, receiver.app.filename)

    def test_pre_start_buffer(self):
        packets, original, _file_hash, _filename = build_packets(self.test_file, chunk_size=400)
        receiver = ReceiverHarness()

        for packet in packets:
            if packet.startswith("DATA|"):
                receiver.feed(packet)

        self.assertFalse(receiver.app.is_collecting)
        self.assertGreater(len(receiver.app.pre_start_buffer), 0)

        receiver.feed(packets[0])
        self.assertTrue(receiver.app.last_success)
        self.assertEqual(receiver.save_result(), original)

    def test_filename_with_pipe_character(self):
        packets, original, _file_hash, filename = build_packets(
            self.test_file,
            chunk_size=350,
            filename_override="my|special|file.txt",
        )
        receiver = ReceiverHarness()
        for packet in packets:
            receiver.feed(packet)

        self.assertEqual(receiver.app.filename, "my|special|file.txt")
        self.assertEqual(receiver.save_result(), original)

    def test_legacy_plain_filename_fallback(self):
        with open(self.test_file, "rb") as handle:
            content = handle.read()
        file_hash = hashlib.sha256(content).hexdigest()
        b64_str = base64.b64encode(content).decode("utf-8")
        parts = [b64_str[i:i + 200] for i in range(0, len(b64_str), 200)]
        packets = [f"START|legacy.txt|{len(parts)}|{file_hash}"]
        packets.extend(f"DATA|{i}|{c}" for i, c in enumerate(parts))

        receiver = ReceiverHarness()
        for packet in packets:
            receiver.feed(packet)

        self.assertEqual(receiver.app.filename, "legacy.txt")
        self.assertEqual(receiver.save_result(), content)

    def test_reject_invalid_extension(self):
        packets, _original, _file_hash, _filename = build_packets(
            self.test_file,
            filename_override="malware.exe",
        )
        receiver = ReceiverHarness()
        receiver.feed(packets[0])
        self.assertFalse(receiver.app.is_collecting)

    def test_second_transfer_after_success(self):
        packets_a, content_a, _hash_a, _name_a = build_packets(self.test_file, chunk_size=250)
        packets_b, content_b, _hash_b, _name_b = build_packets(self.test_file, chunk_size=280)

        receiver = ReceiverHarness()
        for packet in packets_a:
            receiver.feed(packet)
        self.assertTrue(receiver.app.last_success)
        self.assertEqual(receiver.save_result(), content_a)

        receiver.app.last_success = False
        for packet in packets_b:
            receiver.feed(packet)

        self.assertTrue(receiver.app.is_collecting or receiver.app.last_success)
        if receiver.app.last_success:
            self.assertEqual(receiver.save_result(), content_b)

    def test_sender_file_size_limit_constant(self):
        self.assertEqual(MAX_FILE_SIZE_BYTES, 500 * 1024)

    def test_video_export_writes_frames(self):
        import cv2
        from displayBridgeSender import DisplayBridgeApp

        packets, _content, _file_hash, _filename = build_packets(self.test_file, chunk_size=200)
        images = [_worker_generate_qr(packet) for packet in packets]

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = os.path.join(tmp_dir, "bridge.avi")
            w, h = images[0].size
            w, h = (w // 2) * 2, (h // 2) * 2
            writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"), 8, (w, h))
            for index, pil_img in enumerate(images):
                cv_img = cv2.cvtColor(np.array(pil_img.resize((w, h))), cv2.COLOR_RGB2BGR)
                writer.write(cv_img)
                if index == 0 or index == len(images) - 1:
                    writer.write(cv_img)
            writer.release()

            self.assertTrue(os.path.isfile(path))
            self.assertGreater(os.path.getsize(path), 0)

            cap = cv2.VideoCapture(path)
            decoded_packets = set()
            while cap.isOpened():
                ret, frame = cap.read()
                if not ret:
                    break
                for obj in decode(frame):
                    try:
                        decoded_packets.add(obj.data.decode("utf-8"))
                    except Exception:
                        pass
            cap.release()

            self.assertIn(packets[0], decoded_packets)
            self.assertTrue(len(decoded_packets) >= len(packets) - 1)


class WebLogicTests(unittest.TestCase):
  """Executed via Node subprocess in run_web_tests()."""


def run_web_tests():
    import json
    import subprocess

    node_script = r"""
const MAX_CHUNKS = 10000;
const ALLOWED_EXTENSIONS = new Set(['.jpg', '.jpeg', '.png', '.pdf', '.txt', '.zip', '.webp']);

function encodeFilename(filename) {
  return Buffer.from(filename, 'utf8').toString('base64');
}

function decodeFilename(encodedName) {
  try {
    const decoded = decodeURIComponent(Array.from(atob(encodedName), c =>
      '%' + c.charCodeAt(0).toString(16).padStart(2, '0')
    ).join(''));
    if (decoded && !/[\x00-\x08\x0e-\x1f]/.test(decoded)) return decoded;
  } catch (e) {}
  return encodedName;
}

function getExtension(filename) {
  const dot = filename.lastIndexOf('.');
  return dot >= 0 ? filename.slice(dot).toLowerCase() : '';
}

function validateStartPacket(parts) {
  if (parts.length < 4) return false;
  const totalChunks = parseInt(parts[2], 10);
  const expectedHash = parts[3];
  if (!Number.isInteger(totalChunks) || totalChunks <= 0 || totalChunks > MAX_CHUNKS) return false;
  const filename = decodeFilename(parts[1]).split(/[/\\]/).pop().replace(/\x00/g, '');
  const ext = getExtension(filename);
  if (!filename || !ALLOWED_EXTENSIONS.has(ext)) return false;
  if (!/^[a-fA-F0-9]{64}$/.test(expectedHash)) return false;
  return true;
}

function base64ToBytes(b64) {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

const results = [];
function assert(name, condition) {
  results.push({ name, ok: !!condition });
}

const hash = 'a'.repeat(64);
const fn = encodeFilename('test|file.txt');
assert('encode filename', fn.length > 0);
assert('decode filename', decodeFilename(fn) === 'test|file.txt');
assert('validate good start', validateStartPacket(['START', fn, '10', hash]));
assert('reject bad hash', !validateStartPacket(['START', fn, '10', 'bad']));
assert('reject huge chunks', !validateStartPacket(['START', fn, '999999', hash]));
assert('reject bad extension', !validateStartPacket(['START', encodeFilename('x.exe'), '10', hash]));
assert('base64 bytes', base64ToBytes('dGVzdA==').length === 4);

console.log(JSON.stringify(results));
"""

    result = subprocess.run(
        ["node", "-e", node_script],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout.strip())


if __name__ == "__main__":
    print("=" * 60)
    print("DisplayBridge Protocol Tests")
    print("=" * 60)

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ProtocolTests)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("\n" + "=" * 60)
    print("Web Logic Tests (Node.js)")
    print("=" * 60)
    web_failures = []
    try:
        web_results = run_web_tests()
        for item in web_results:
            status = "PASS" if item["ok"] else "FAIL"
            print(f"  [{status}] {item['name']}")
            if not item["ok"]:
                web_failures.append(item["name"])
    except Exception as exc:
        print(f"  [FAIL] Web test runner error: {exc}")
        web_failures.append("runner")

    total_failures = len(result.failures) + len(result.errors) + len(web_failures)
    print("\n" + "=" * 60)
    if total_failures == 0:
        print("ALL TESTS PASSED")
        sys.exit(0)
    print(f"FAILED: {total_failures} test(s)")
    sys.exit(1)
