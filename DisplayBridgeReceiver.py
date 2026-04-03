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
    - File Mode: Scans every frame of a video or a static image using a frame-by-frame iteration logic.
2.  DECODING:
    - Uses the ZBar library (pyzbar) to identify and decode QR codes within each frame.
    - Extracts structured data strings formatted as 'TYPE|METADATA|PAYLOAD'.
3.  RECONSTRUCTION:
    - Buffers incoming Base64-encoded chunks into a dictionary to handle out-of-order delivery or redundant captures.
    - Once the total chunk count (defined in the 'START' packet) is reached, the application concatenates the segments and decodes the Base64 string back into the original binary file.
4.  AUTO-SAVE:
    - Automatically writes the reconstructed file to the user's ~/Downloads directory using the original filename.
5.  SECURITY ARCHITECTURE:
	a. INPUT SANITIZATION: Uses Regex to strip potentially malicious characters from filenames, preventing shell injection and filesystem attacks.
	b. PATH TRAVERSAL PROTECTION: Forces filenames into a flat structure using os.path.basename, ensuring files cannot be written outside the target directory.
	c. RESOURCE QUOTAS: Implements MAX_CHUNKS and MAX_FILE_SIZE_MB to prevent Memory-Exhaustion (DoS) attacks via manipulated QR metadata.
	d. TYPE WHITELISTING: Restricts reconstruction to a predefined list of safe file extensions (.jpg, .pdf, .txt, etc.).

FUNCTIONAL FEATURES:
- DUAL INPUT: Supports both real-time webcam scanning and file-based import.
- SMART FILTER: Validates file extensions (Video/Image) for Drag & Drop and file selection to ensure system stability.
- UI FEEDBACK: Real-time progress tracking, visual QR detection markers, and a persistent log area for status and error reporting.
- DRAG & DROP: Integrated TkinterDnD support for intuitive file processing.
"""

import subprocess
import sys
import os
import base64
import re  # Added for Sanitizing
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
        self.root.title("DisplayBridge - Receiver")
        self.root.geometry("900x1000")
        self.root.configure(bg="#f5f5f5")

        # Security limits
        self.MAX_FILE_SIZE_MB = 200
        self.MAX_CHUNKS = 10000
        self.ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.pdf', '.txt', '.zip'} 

        # Logic State
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
        # Header Area
        self.header_frame = tk.Frame(self.root, bg="#f5f5f5")
        self.header_frame.pack(fill="x", padx=20, pady=5)

        # Drop Zone
        txt = "DROP VIDEO/IMAGE HERE OR CLICK" if DND_AVAILABLE else "CLICK TO OPEN FILE"
        self.drop_label = tk.Label(self.header_frame, text=txt, 
                                  bg="#ffffff", fg="#007bff", height=2, 
                                  relief="ridge", bd=2, font=("Arial", 10, "bold"))
        self.drop_label.pack(fill="x", pady=5)
        
        if DND_AVAILABLE:
            self.drop_label.drop_target_register(DND_FILES)
            self.drop_label.dnd_bind('<<Drop>>', self.on_file_drop)
        self.drop_label.bind("<Button-1>", lambda e: self.open_file_dialog())

        # Status & Control Panel
        self.control_panel = tk.Frame(self.header_frame, bg="#e9ecef", relief="flat", padx=10, pady=5)
        self.control_panel.pack(fill="x", pady=5)
        
        self.status_var = tk.StringVar(value="Status: Ready to Receive")
        tk.Label(self.control_panel, textvariable=self.status_var, font=("Arial", 8), bg="#e9ecef", anchor="w").pack(fill="x")

        # Log Area
        self.log_frame = tk.LabelFrame(self.header_frame, text=" Transmission Log ", bg="#f5f5f5", font=("Arial", 9, "bold"))
        self.log_frame.pack(fill="x", pady=5)
        
        self.log_text = tk.Text(self.log_frame, height=4, bg="#ffffff", font=("Courier", 8), state="disabled", relief="flat")
        self.log_text.pack(fill="x", padx=5, pady=5)

        # Buttons & Counter Frame
        self.btn_frame = tk.Frame(self.header_frame, bg="#f5f5f5")
        self.btn_frame.pack(fill="x", pady=5)

        self.btn_start_cam = tk.Button(self.btn_frame, text="▶ START CAM", command=self.start_camera, 
                                      bg="#d4edda", fg="#155724", font=("Arial", 9, "bold"), width=12)
        self.btn_start_cam.pack(side="left", padx=5)

        self.btn_stop_cam = tk.Button(self.btn_frame, text="⏹ STOP", command=self.stop_camera, 
                                     bg="#f8d7da", fg="#721c24", font=("Arial", 9, "bold"), width=10, state="disabled")
        self.btn_stop_cam.pack(side="left", padx=5)

        self.btn_clear = tk.Button(self.btn_frame, text="🗑 CLEAR", command=self.reset_ui_state, 
                                  bg="#fff3cd", fg="#856404", font=("Arial", 9, "bold"), width=10)
        self.btn_clear.pack(side="left", padx=5)

        # Counter (Large Red)
        self.progress_var = tk.StringVar(value="0 / 0")
        self.counter_label = tk.Label(self.btn_frame, textvariable=self.progress_var, 
                                     font=("Arial", 16, "bold"), bg="#f5f5f5", fg="#e63946")
        self.counter_label.pack(side="right")

        # Progress Bar
        self.progress_bar = ttk.Progressbar(self.header_frame, orient="horizontal", mode="determinate")
        self.progress_bar.pack(fill="x", pady=5)

        self.notify_var = tk.StringVar(value="")
        self.notify_label = tk.Label(self.header_frame, textvariable=self.notify_var, 
                                    fg="#28a745", bg="#f5f5f5", font=("Arial", 10, "bold"))
        self.notify_label.pack(pady=2)

        # Main Preview Area (Black Box)
        self.cam_frame = tk.Frame(self.root, bg="black", highlightthickness=1, highlightbackground="#333")
        self.cam_frame.pack(expand=True, fill="both", padx=10, pady=10)
        
        self.cam_label = tk.Label(self.cam_frame, bg="black", text="[ NO SIGNAL ]", fg="#444")
        self.cam_label.pack(expand=True, fill="both")

    def on_file_drop(self, event):
        path = event.data.strip('{}').strip()
        if os.path.isfile(path): self.process_input_file(path)

    def open_file_dialog(self):
        file_types = [("Media Files", "*.avi *.mp4 *.mkv *.mov *.jpg *.jpeg *.png *.bmp *.webp")]
        path = filedialog.askopenfilename(file_types=file_types)
        if path: self.process_input_file(path)

    def process_input_file(self, path):
        ext = os.path.splitext(path)[1].lower()
        if ext in self.valid_video_exts:
            self.process_video(path)
        elif ext in self.valid_image_exts:
            self.process_image(path)
        else:
            self.log_message(f"Format not supported: {ext}")

    def process_image(self, path):
        self.reset_ui_state()
        self.log_message(f"Scanning Image: {os.path.basename(path)}")
        frame = cv2.imread(path)
        if frame is not None:
            for obj in decode(frame):
                try: self.process_qr_data(obj.data.decode('utf-8'))
                except: continue
            self.show_frame(frame)
        if not self.last_success: self.log_message("No QR data found.")

    def process_video(self, path):
        self.reset_ui_state()
        self.status_var.set("Status: Scanning Video File...")
        self.log_message(f"Scanning Video: {os.path.basename(path)}")
        cap = cv2.VideoCapture(path)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        frame_idx = 0

        while cap.isOpened():
            ret, frame = cap.read()
            if not ret or self.last_success: break
            frame_idx += 1
            for obj in decode(frame):
                try: self.process_qr_data(obj.data.decode('utf-8'))
                except: continue
            if frame_idx % 20 == 0:
                self.progress_bar["value"] = (frame_idx / total_frames) * 100
                self.root.update()
        cap.release()

    def start_camera(self):
        self.cap = cv2.VideoCapture(0)
        if self.cap.isOpened():
            self.is_cam_on = True
            self.last_success = False
            self.btn_start_cam.config(state="disabled")
            self.btn_stop_cam.config(state="normal")
            self.status_var.set("Status: Live Scan Active")
            self.notify_var.set("")
            self.update_frame()

    def stop_camera(self):
        self.is_cam_on = False
        if self.cap: self.cap.release()
        self.btn_start_cam.config(state="normal")
        self.btn_stop_cam.config(state="disabled")
        self.status_var.set("Status: Camera Off")

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
            self.show_frame(frame)
        
        if self.last_success: self.stop_camera()
        else: self.root.after(15, self.update_frame)

    def show_frame(self, frame):
        # Maintain aspect ratio for preview
        img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        img.thumbnail((800, 600), Image.LANCZOS)
        imgtk = ImageTk.PhotoImage(image=img)
        self.cam_label.imgtk = imgtk
        self.cam_label.configure(image=imgtk, text="")

    def process_qr_data(self, data):
        try:
            parts = data.split('|')
            
            # 1. VALIDATION OF START PACKET
            if parts[0] == "START" and not self.is_collecting:
                raw_filename = parts[1]
                num_chunks = int(parts[2])

                if num_chunks > self.MAX_CHUNKS or num_chunks <= 0:
                    self.log_message("SECURITY ALERT: Invalid chunk count rejected.")
                    return

                clean_filename = os.path.basename(raw_filename)
                clean_filename = re.sub(r'(?u)[^-\w.]', '', clean_filename)
                
                ext = os.path.splitext(clean_filename)[1].lower()
                if ext not in self.ALLOWED_EXTENSIONS:
                    self.log_message(f"SECURITY ALERT: Extension {ext} not allowed.")
                    return

                self.filename = clean_filename
                self.total_chunks = num_chunks
                
                # Progressbar korrekt initialisieren
                self.progress_bar.config(maximum=self.total_chunks, value=0)
                
                self.is_collecting = True
                self.status_var.set(f"Status: Receiving {self.filename}")
                self.log_message(f"Detected: {self.filename} ({self.total_chunks} chunks)")
            
            # 2. VALIDATION OF DATA PACKETS
            elif parts[0] == "DATA" and self.is_collecting:
                idx = int(parts[1])
                payload = parts[2]

                if not (0 <= idx < self.total_chunks):
                    return

                if idx not in self.received_chunks:
                    if (len(self.received_chunks) * 3000) > (self.MAX_FILE_SIZE_MB * 1024 * 1024):
                        self.log_message("SECURITY ALERT: File size limit exceeded.")
                        self.reset_ui_state()
                        return

                    self.received_chunks[idx] = payload
                    count = len(self.received_chunks)
                    
                    # Fortschritt aktualisieren
                    self.progress_bar["value"] = count
                    self.progress_var.set(f"{count} / {self.total_chunks}")
                    self.root.update_idletasks() # Erzwingt das visuelle Update der UI
                    
                    if count == self.total_chunks:
                        self.save_and_finish()
        except Exception:
            self.log_message(f"Security Filter: Invalid packet discarded.")

    def save_and_finish(self):
        try:
            full_b64 = "".join([self.received_chunks[i] for i in range(self.total_chunks)])
            file_bytes = base64.b64decode(full_b64)
            path = os.path.join(os.path.expanduser("~"), "Downloads", self.filename)
            with open(path, "wb") as f: f.write(file_bytes)
            
            self.last_success = True
            self.notify_var.set("✔ FILE RECONSTRUCTED & SAVED")
            self.log_message(f"SUCCESS: Saved to {path}")
            self.status_var.set("Status: Download Complete")
        except Exception as e:
            self.log_message(f"Save Error: {e}")

    def log_message(self, msg):
        self.log_text.config(state="normal")
        self.log_text.insert("1.0", f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
        self.log_text.config(state="disabled")

    def reset_ui_state(self):
        self.stop_camera()
        self.received_chunks = {}; self.is_collecting = False; self.last_success = False
        self.progress_bar["value"] = 0
        self.progress_var.set("0 / 0")
        self.notify_var.set("")
        self.status_var.set("Status: Ready")
        self.cam_label.configure(image='', text="[ NO SIGNAL ]")

if __name__ == "__main__":
    root = TkinterDnD.Tk() if DND_AVAILABLE else tk.Tk()
    app = ReceiverApp(root)
    root.mainloop()
