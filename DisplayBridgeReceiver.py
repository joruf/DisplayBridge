#!/usr/bin/env python3
"""
Displaybridge - RECEIVER
========================
DESCRIPTION:
This application acts as a digital bridge to reconstruct files transmitted 
via QR code sequences. It captures visual data through a live webcam feed 
or by processing existing video/image files to retrieve encoded data packets.

TECHNICAL OPERATION:
1.  ACQUISITION: 
    - Live Mode: Utilizes OpenCV (cv2) to capture real-time camera frames.
    - File Mode: Scans every frame of a video or a static image using a 
      frame-by-frame iteration logic.
2.  DECODING: 
    - Uses the ZBar library (pyzbar) to identify and decode QR codes within 
      each frame.
    - Extracts structured data strings formatted as 'TYPE|METADATA|PAYLOAD'.
3.  RECONSTRUCTION:
    - Buffers incoming Base64-encoded chunks into a dictionary to handle 
      out-of-order delivery or redundant captures.
    - Once the total chunk count (defined in the 'START' packet) is reached, 
      the application concatenates the segments and decodes the Base64 
      string back into the original binary file.
4.  AUTO-SAVE: 
    - Automatically writes the reconstructed file to the user's ~/Downloads 
      directory using the original filename.

FUNCTIONAL FEATURES:
- DUAL INPUT: Supports both real-time webcam scanning and file-based import.
- SMART FILTER: Validates file extensions (Video/Image) for Drag & Drop 
  and file selection to ensure system stability.
- UI FEEDBACK: Real-time progress tracking, visual QR detection markers, 
  and a persistent log area for status and error reporting.
- DRAG & DROP: Integrated TkinterDnD support for intuitive file processing.
"""

import subprocess
import sys
import os
import base64
import tkinter as tk
from tkinter import ttk, filedialog
import numpy as np
from datetime import datetime

# --- PART 1: DEPENDENCIES ---
def ensure_dependencies():
    try:
        import cv2
        from pyzbar.pyzbar import decode
        from PIL import Image, ImageTk
    except ImportError:
        try:
            print("Installing Receiver dependencies...")
            dependencies = ['opencv-python', 'pyzbar', 'pillow', 'numpy<1.28', 'tkinterdnd2-universal']
            subprocess.run([sys.executable, '-m', 'pip', 'install', *dependencies, '--break-system-packages'], check=True)
            os.execv(sys.executable, ['python3'] + sys.argv)
        except Exception as e:
            print(f"Error: {e}"); sys.exit(1)

ensure_dependencies()

import cv2
from pyzbar.pyzbar import decode
from PIL import Image, ImageTk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False

# --- PART 2: RECEIVER LOGIC ---
class ReceiverApp:
    def __init__(self, root):
        self.root = root
        self.root.title("DisplayBridge Receiver v2.5")
        self.root.geometry("850x950")
        self.root.configure(bg="#f0f0f0")

        # Erlaubte Formate
        self.valid_video_exts = {'.avi', '.mp4', '.mkv', '.mov', '.wmv'}
        self.valid_image_exts = {'.jpg', '.jpeg', '.png', '.bmp', '.webp', '.tiff'}

        self.filename = ""
        self.total_chunks = 0
        self.received_chunks = {}
        self.is_collecting = False
        self.is_cam_on = False
        self.cap = None
        self.last_success = False 

        self.setup_ui()

    def setup_ui(self):
        self.info_frame = tk.Frame(self.root, bg="#2c3e50", pady=15)
        self.info_frame.pack(fill="x")

        self.status_var = tk.StringVar(value="BEREIT: BILD/VIDEO ABLEGEN ODER CAM STARTEN")
        tk.Label(self.info_frame, textvariable=self.status_var, fg="#1abc9c", bg="#2c3e50", font=("Arial", 12, "bold")).pack()

        self.progress_var = tk.StringVar(value="Fortschritt: 0 / 0 Chunks")
        tk.Label(self.info_frame, textvariable=self.progress_var, fg="#bdc3c7", bg="#2c3e50").pack()

        self.progress_bar = ttk.Progressbar(self.root, orient="horizontal", mode="determinate")
        self.progress_bar.pack(fill="x", padx=30, pady=15)

        self.drop_frame = tk.Frame(self.root, bg="#bdc3c7", bd=2, relief="groove")
        self.drop_frame.pack(fill="x", padx=30, pady=10)

        txt = "➔ BILD/VIDEO HIER ABLEGEN ODER KLICKEN" if DND_AVAILABLE else "➔ KLICKEN ZUM ÖFFNEN"
        self.drop_label = tk.Label(self.drop_frame, text=txt, 
                                  bg="#ffffff", fg="#2980b9", height=4, 
                                  font=("Arial", 11, "bold"), cursor="hand2")
        self.drop_label.pack(fill="x", padx=5, pady=5)

        if DND_AVAILABLE:
            self.drop_label.drop_target_register(DND_FILES)
            self.drop_label.dnd_bind('<<Drop>>', self.on_file_drop)
        
        self.drop_label.bind("<Button-1>", lambda e: self.open_file_dialog())

        self.cam_frame = tk.Frame(self.root, bg="black", bd=2, relief="sunken")
        self.cam_frame.pack(expand=True, fill="both", padx=20, pady=10)
        
        self.cam_label = tk.Label(self.cam_frame, bg="black", text="Scan-Vorschau", fg="white")
        self.cam_label.pack(expand=True, fill="both")

        self.btn_frame = tk.Frame(self.root, bg="#f0f0f0", pady=10)
        self.btn_frame.pack(fill="x")

        self.btn_start_cam = tk.Button(self.btn_frame, text="▶ KAMERA STARTEN", command=self.start_camera, 
                                      bg="#27ae60", fg="white", font=("Arial", 10, "bold"), padx=15)
        self.btn_start_cam.pack(side="left", padx=20)

        self.btn_stop_cam = tk.Button(self.btn_frame, text="⏹ STOPP", command=self.stop_camera, 
                                     bg="#e74c3c", fg="white", font=("Arial", 10, "bold"), padx=15, state="disabled")
        self.btn_stop_cam.pack(side="left")

        self.log_frame = tk.LabelFrame(self.root, text=" Log-Verlauf & Status ", bg="#f0f0f0")
        self.log_frame.pack(fill="x", padx=20, pady=10)
        
        self.log_text = tk.Text(self.log_frame, height=10, bg="#dfe6e9", font=("Courier", 9), state="disabled")
        self.log_text.pack(fill="x", padx=5, pady=5)

    def on_file_drop(self, event):
        path = event.data.strip('{}').strip()
        if os.path.isfile(path): self.process_input_file(path)

    def open_file_dialog(self):
        # Filter im Dialog für bessere UX
        file_types = [
            ("Media Files", "*.avi *.mp4 *.mkv *.mov *.jpg *.jpeg *.png *.bmp *.webp"),
            ("Videos", "*.avi *.mp4 *.mkv *.mov"),
            ("Images", "*.jpg *.jpeg *.png *.bmp *.webp"),
            ("All files", "*.*")
        ]
        path = filedialog.askopenfilename(filetypes=file_types)
        if path: self.process_input_file(path)

    def process_input_file(self, path):
        ext = os.path.splitext(path)[1].lower()
        
        if ext in self.valid_video_exts:
            self.process_video(path)
        elif ext in self.valid_image_exts:
            self.process_image(path)
        else:
            self.log_message(f"IGNORIERT: '{os.path.basename(path)}' ist kein unterstütztes Bild- oder Videoformat.")
            self.status_var.set("UNTERSTÜTZTES FORMAT WÄHLEN")

    def process_image(self, path):
        self.reset_ui_state()
        self.last_success = False
        self.log_message(f"Scanne Bild: {os.path.basename(path)}")
        
        frame = cv2.imread(path)
        if frame is None:
            self.log_message("FEHLER: Bild konnte nicht geladen werden.")
            return

        for obj in decode(frame):
            try:
                self.process_qr_data(obj.data.decode('utf-8'))
            except: continue
        
        if not self.last_success:
            self.log_message("INFO: Keine QR-Daten im Bild gefunden.")

    def process_video(self, path):
        self.reset_ui_state()
        self.last_success = False
        self.status_var.set("VIDEO-SCAN LÄUFT...")
        self.log_message(f"Scanne Video: {os.path.basename(path)}")
        
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            self.log_message("FEHLER: Video konnte nicht geöffnet werden.")
            return

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        frame_idx = 0

        while cap.isOpened():
            ret, frame = cap.read()
            if not ret: break
            frame_idx += 1
            
            for obj in decode(frame):
                try:
                    self.process_qr_data(obj.data.decode('utf-8'))
                except: continue
            
            if self.last_success: break
            
            if frame_idx % 20 == 0:
                if not self.is_collecting:
                    self.progress_bar["value"] = (frame_idx / total_frames) * 100
                self.root.update()

        cap.release()
        if not self.last_success:
            self.log_message("INFO: Scan beendet. Keine vollständigen Daten gefunden.")

    def start_camera(self):
        self.cap = cv2.VideoCapture(0)
        if self.cap.isOpened():
            self.is_cam_on = True
            self.last_success = False
            self.btn_start_cam.config(state="disabled")
            self.btn_stop_cam.config(state="normal")
            self.status_var.set("SCANNE LIVE-BILD...")
            self.log_message("Kamera aktiv...")
            self.update_frame()

    def stop_camera(self):
        self.is_cam_on = False
        if self.cap: self.cap.release()
        self.btn_start_cam.config(state="normal")
        self.btn_stop_cam.config(state="disabled")
        self.status_var.set("KAMERA AUS")

    def update_frame(self):
        if not self.is_cam_on: return
        ret, frame = self.cap.read()
        if ret:
            for obj in decode(frame):
                try:
                    self.process_qr_data(obj.data.decode('utf-8'))
                    pts = np.array([obj.polygon], np.int32)
                    cv2.polylines(frame, [pts], True, (0, 255, 0), 2)
                except: continue

            img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            imgtk = ImageTk.PhotoImage(image=img.resize((640, 480)))
            self.cam_label.imgtk = imgtk
            self.cam_label.configure(image=imgtk)
        
        if self.last_success: self.stop_camera()
        else: self.root.after(15, self.update_frame)

    def process_qr_data(self, data):
        try:
            parts = data.split('|')
            if parts[0] == "START" and not self.is_collecting:
                self.filename, self.total_chunks = parts[1], int(parts[2])
                self.progress_bar["maximum"] = self.total_chunks
                self.is_collecting = True
                self.status_var.set(f"EMPFANGE: {self.filename}")
                self.log_message(f"Daten gefunden: {self.filename} ({self.total_chunks} Chunks)")
            
            elif parts[0] == "DATA" and self.is_collecting:
                idx = int(parts[1])
                if idx not in self.received_chunks:
                    self.received_chunks[idx] = parts[2]
                    count = len(self.received_chunks)
                    self.progress_bar["value"] = count
                    self.progress_var.set(f"Fortschritt: {count} / {self.total_chunks}")
                    if count == self.total_chunks:
                        self.save_and_finish()
        except: pass

    def save_and_finish(self):
        try:
            full_b64 = "".join([self.received_chunks[i] for i in range(self.total_chunks)])
            file_bytes = base64.b64decode(full_b64)
            path = os.path.join(os.path.expanduser("~"), "Downloads", self.filename)
            with open(path, "wb") as f: f.write(file_bytes)
            
            self.last_success = True
            self.log_message("*" * 40)
            self.log_message(f"ERFOLG: '{self.filename}' rekonstruiert.")
            self.log_message(f"PFAD: {path}")
            self.log_message("*" * 40)
            self.status_var.set("FERTIG: GESPEICHERT")
            self.reset_logic()
        except Exception as e:
            self.log_message(f"FEHLER beim Speichern: {str(e)}")

    def log_message(self, msg):
        self.log_text.config(state="normal")
        self.log_text.insert("1.0", f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
        self.log_text.config(state="disabled")

    def reset_ui_state(self):
        self.received_chunks = {}; self.is_collecting = False
        self.progress_bar["value"] = 0
        self.progress_var.set("Fortschritt: 0 / 0 Chunks")

    def reset_logic(self):
        self.received_chunks = {}; self.is_collecting = False
        self.root.after(3000, self._finalize_ui)

    def _finalize_ui(self):
        if self.last_success:
            self.progress_bar["value"] = 0
            self.status_var.set("BEREIT")

if __name__ == "__main__":
    root = TkinterDnD.Tk() if DND_AVAILABLE else tk.Tk()
    app = ReceiverApp(root)
    root.mainloop()
