#!/usr/bin/env python3
"""
DisplayBridge - SENDER
======================
DESCRIPTION:
This application converts any binary file into a high-speed sequence of 
QR codes. It acts as the transmission source, allowing data to be 
"beamed" from a screen to a receiver (camera or video file).

TECHNICAL OPERATION:
1.  ENCODING & FRAGMENTATION:
    - Reads the input file as raw binary and encodes it into a single 
      Base64 string to ensure 7-bit ASCII compatibility for QR generation.
    - Slices the string into fixed-size segments (chunks) to fit within 
      QR code density limits.
2.  PROTOCOL WRAPPING:
    - START Packet: Generates a header frame containing the original 
      filename and the total number of expected chunks.
    - DATA Packets: Wraps each payload chunk with a sequence index 
      ('DATA|index|payload') to allow for unordered reconstruction.
3.  RELIABLE VIDEO EXPORT:
    - Frame Redundancy: Duplicates the first (Header) and last (Footer) 
      frames in the video export to ensure the receiver catches the 
      critical start/end signals.
    - MJPG Implementation: Uses the Motion JPEG codec at 100% quality 
      to provide lossless intra-frame compression, ensuring QR edges 
      remain perfectly sharp for scanning.
4.  VISUAL OPTIMIZATION:
    - Employs 'NEAREST' neighbor interpolation for scaling, preventing 
      anti-aliasing "blur" that typically breaks QR recognition at 
      high resolutions.
    - Dynamic FPS control to match the processing capabilities of the 
      receiving device.

FUNCTIONAL FEATURES:
- DRAG & DROP: Built with TkinterDnD for seamless file importing.
- DUAL OUTPUT: Offers both a live on-screen loop animation and an 
  optimized .avi video export.
- RELIABILITY FOCUS: Automated redundancy and sharp-edge rendering 
  specifically tuned for 2026-era high-res displays.
"""

import subprocess
import sys
import os
import base64
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import numpy as np

# Speed presets: (Label, FPS-Value)
SPEED_PRESETS = [
    ("2 FPS", 2), ("4 FPS", 4), ("8 FPS", 8), ("15 FPS", 15), ("30 FPS", 30)
]
DEFAULT_FPS = 15 

# --- PART 1: DEPENDENCIES ---
def ensure_dependencies():
    try:
        import qrcode
        import cv2
        from PIL import Image, ImageTk
        from tkinterdnd2 import DND_FILES, TkinterDnD
    except ImportError:
        try:
            print("Installing/Updating dependencies. Please wait...")
            dependencies = ['qrcode[pil]', 'tkinterdnd2', 'opencv-python', 'pillow', 'numpy<1.28']
            subprocess.run([sys.executable, '-m', 'pip', 'install', *dependencies, '--break-system-packages'], check=True)
            os.execv(sys.executable, ['python3'] + sys.argv)
        except Exception as e:
            print(f"Failed to install dependencies: {e}"); sys.exit(1)

ensure_dependencies()

import cv2
from qrcode import QRCode, constants
from PIL import Image, ImageTk
from tkinterdnd2 import DND_FILES, TkinterDnD

# --- PART 2: MAIN APPLICATION ---
class DisplayBridgeApp:
    def __init__(self, root):
        self.root = root
        self.root.title("DisplayBridge v4.2 - Reliability QR Sender")
        self.root.geometry("900x1000")
        self.root.configure(bg="#f5f5f5")
        
        self.chunk_size = 480  
        self.raw_qr_images = [] 
        self.tk_images = []     
        self.current_idx = 0
        self.is_running = False
        self.file_loaded = False
        self.filename = ""

        self.setup_ui()

    def setup_ui(self):
        # Header & Drop Zone
        self.header_frame = tk.Frame(self.root, bg="#f5f5f5")
        self.header_frame.pack(fill="x", padx=20, pady=5)

        self.drop_label = tk.Label(self.header_frame, text="DROP FILE HERE OR CLICK", 
                                  bg="#ffffff", fg="#007bff", height=2, 
                                  relief="ridge", bd=2, font=("Arial", 10, "bold"))
        self.drop_label.pack(fill="x", pady=5)
        self.drop_label.drop_target_register(DND_FILES)
        self.drop_label.dnd_bind('<<Drop>>', self.on_file_drop)
        self.drop_label.bind("<Button-1>", lambda e: self.open_file_dialog())

        # Path Display
        self.control_panel = tk.Frame(self.header_frame, bg="#e9ecef", relief="flat", padx=10, pady=5)
        self.control_panel.pack(fill="x", pady=5)
        self.path_var = tk.StringVar(value="File: None selected")
        tk.Label(self.control_panel, textvariable=self.path_var, font=("Arial", 8), bg="#e9ecef", anchor="w").pack(fill="x")

        # Speed Selector
        self.speed_frame = tk.LabelFrame(self.header_frame, text=" Transmission Speed (FPS) ", bg="#f5f5f5", font=("Arial", 9, "bold"))
        self.speed_frame.pack(fill="x", pady=5)
        self.fps_var = tk.IntVar(value=DEFAULT_FPS) 
        for label, fps in SPEED_PRESETS:
            tk.Radiobutton(self.speed_frame, text=label, variable=self.fps_var, value=fps, 
                           bg="#f5f5f5", font=("Arial", 9)).pack(side="left", padx=15)

        # Control Buttons
        self.btn_frame = tk.Frame(self.header_frame, bg="#f5f5f5")
        self.btn_frame.pack(fill="x", pady=5)

        self.btn_start = tk.Button(self.btn_frame, text="▶ START", bg="#d4edda", command=self.start_anim, state="disabled", width=10)
        self.btn_start.pack(side="left", padx=5)
        self.btn_stop = tk.Button(self.btn_frame, text="⏸ STOP", bg="#fff3cd", command=self.stop_anim, state="disabled", width=10)
        self.btn_stop.pack(side="left", padx=5)
        
        # Optimized Export Button
        self.btn_export = tk.Button(self.btn_frame, text="🎬 EXPORT RELIABLE VIDEO", bg="#17a2b8", fg="white", 
                                   command=self.export_as_video, state="disabled", width=25, font=("Arial", 9, "bold"))
        self.btn_export.pack(side="left", padx=5)
        
        self.btn_clear = tk.Button(self.btn_frame, text="🗑 CLEAR", bg="#f8d7da", command=self.clear_all, width=10)
        self.btn_clear.pack(side="left", padx=5)

        # Counter & Progress
        self.counter_var = tk.StringVar(value="0 / 0")
        tk.Label(self.btn_frame, textvariable=self.counter_var, font=("Arial", 16, "bold"), bg="#f5f5f5", fg="#e63946").pack(side="right")
        self.progress_bar = ttk.Progressbar(self.header_frame, orient="horizontal", mode="determinate")
        self.progress_bar.pack(fill="x", pady=5)

        self.notify_var = tk.StringVar(value="")
        self.notify_label = tk.Label(self.header_frame, textvariable=self.notify_var, fg="#28a745", bg="#f5f5f5", font=("Arial", 10, "bold"))
        self.notify_label.pack(pady=2)

        # Main QR Display Area
        self.display_frame = tk.Frame(self.root, bg="white", highlightthickness=1, highlightbackground="#333")
        self.display_frame.pack(expand=True, fill="both", padx=10, pady=10)
        self.qr_label = tk.Label(self.display_frame, bg="white")
        self.qr_label.pack(expand=True, fill="both")
        self.display_frame.bind("<Configure>", self.on_resize)

    def on_resize(self, event):
        if self.file_loaded: self.update_qr_scaling()

    def update_qr_scaling(self):
        win_w, win_h = self.display_frame.winfo_width()-30, self.display_frame.winfo_height()-30
        if win_w < 100 or win_h < 100: return 
        new_size = min(win_w, win_h)
        # Use NEAREST for scaling to keep edges sharp
        self.tk_images = [ImageTk.PhotoImage(img.resize((new_size, new_size), Image.NEAREST)) for img in self.raw_qr_images]
        if not self.is_running and self.tk_images: self.show_current_qr()

    def on_file_drop(self, event):
        path = event.data.strip('{}').strip()
        if os.path.isfile(path): self.process_file(path)

    def open_file_dialog(self):
        if not self.file_loaded:
            path = filedialog.askopenfilename()
            if path: self.process_file(path)

    def process_file(self, path):
        self.clear_all(); self.filename = os.path.basename(path); self.path_var.set(f"Path: {path}")
        try:
            with open(path, "rb") as f: b64_str = base64.b64encode(f.read()).decode('utf-8')
            parts = [b64_str[i:i+self.chunk_size] for i in range(0, len(b64_str), self.chunk_size)]
            
            # Create Protocol Sequence
            raw_chunks = [f"START|{self.filename}|{len(parts)}"] + [f"DATA|{i}|{c}" for i, c in enumerate(parts)]
            
            self.raw_qr_images = [self.generate_pil_qr(c) for c in raw_chunks]
            self.progress_bar["maximum"] = len(self.raw_qr_images)
            self.file_loaded = True
            
            self.btn_start.config(state="normal")
            self.btn_stop.config(state="normal")
            self.btn_export.config(state="normal")
            
            self.update_qr_scaling(); self.start_anim()
        except Exception as e: messagebox.showerror("Error", f"Processing failed: {e}")

    def generate_pil_qr(self, data):
        qr = QRCode(version=None, error_correction=constants.ERROR_CORRECT_L, box_size=10, border=2)
        qr.add_data(data); qr.make(fit=True)
        return qr.make_image(fill_color="black", back_color="white").convert('RGB')

    def export_as_video(self):
        """Exports the sequence as an AVI video with duplicated first and last frames."""
        if not self.file_loaded or not self.raw_qr_images: return
        try:
            downloads_path = os.path.join(os.path.expanduser("~"), "Downloads")
            full_path = os.path.join(downloads_path, f"{self.filename}_bridge.avi")
            fps = self.fps_var.get()
            
            # Ensure dimensions are even (required by most encoders)
            w, h = self.raw_qr_images[0].size
            w, h = (w // 2) * 2, (h // 2) * 2
            
            # MJPG (Motion JPEG) is ideal for QR: No compression artifacts between frames.
            fourcc = cv2.VideoWriter_fourcc(*'MJPG')
            video = cv2.VideoWriter(full_path, fourcc, fps, (w, h))
            
            # Set maximum quality flag
            video.set(cv2.VIDEOWRITER_PROP_QUALITY, 100)

            if not video.isOpened():
                raise Exception("Could not open VideoWriter. Check your system's OpenCV codecs.")

            total_frames = len(self.raw_qr_images)
            for idx, pil_img in enumerate(self.raw_qr_images):
                # Resize with NEAREST to keep pixel edges sharp
                img_resized = pil_img.resize((w, h), Image.NEAREST)
                cv_img = cv2.cvtColor(np.array(img_resized), cv2.COLOR_RGB2BGR)
                
                # Logic: Write frame once, but if it's the first or last, write it a second time.
                video.write(cv_img)
                
                if idx == 0 or idx == total_frames - 1:
                    video.write(cv_img) # Duplicate frame

            video.release()
            self.notify_var.set(f"✔ VIDEO SAVED (START/END DUPLICATED)")
            self.root.after(3000, lambda: self.notify_var.set(""))
        except Exception as e:
            messagebox.showerror("Export Error", f"Video creation failed: {e}")

    def show_current_qr(self):
        if self.tk_images:
            idx = self.current_idx % len(self.tk_images)
            self.qr_label.config(image=self.tk_images[idx]); self.qr_label.image = self.tk_images[idx]

    def start_anim(self):
        if self.file_loaded and not self.is_running:
            self.is_running = True; self.animate()

    def stop_anim(self): self.is_running = False

    def clear_all(self):
        self.is_running = False; self.file_loaded = False; self.raw_qr_images = []; self.tk_images = []
        self.current_idx = 0; self.qr_label.config(image=''); self.path_var.set("File: None selected")
        self.counter_var.set("0 / 0"); self.progress_bar["value"] = 0
        self.btn_start.config(state="disabled"); self.btn_stop.config(state="disabled"); self.btn_export.config(state="disabled")

    def animate(self):
        if self.is_running and self.tk_images:
            self.show_current_qr()
            self.counter_var.set(f"{self.current_idx + 1} / {len(self.tk_images)}")
            self.progress_bar["value"] = self.current_idx + 1
            self.current_idx = (self.current_idx + 1) % len(self.tk_images)
            # Dynamic delay based on UI speed setting
            self.root.after(int(1000 / self.fps_var.get()), self.animate)

if __name__ == "__main__":
    try:
        # Use TkinterDnD for drag and drop support
        root = TkinterDnD.Tk(); app = DisplayBridgeApp(root); root.mainloop()
    except Exception as e: print(f"Fatal Error: {e}")
