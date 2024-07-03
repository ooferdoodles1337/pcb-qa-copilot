import os
import time
import queue
import threading
import tkinter as tk
from tkinter import filedialog

import cv2
import numpy as np
from PIL import Image, ImageTk
from skimage.exposure import match_histograms
from skimage.metrics import structural_similarity

from changechip import pipeline
from widgets import PanZoomCanvas


class PCBQualityAssuranceApp:
    def __init__(self, root):
        print("Starting App")
        self.root = root
        self.root.title("PCB Quality Assurance Toolkit")
        self.window_width = 1600
        self.window_height = 900
        self.root.geometry(f"{self.window_width}x{self.window_height}")
        self.root.minsize(1000, 600)
        self.root.config()

        self.reference_image = None
        self.current_frame = None
        self.processed_frame = None
        self.frame_queue = queue.Queue(maxsize=1)  # Queue to hold frames for processing
        self.flicker_state = True

        self.cap = cv2.VideoCapture(1, cv2.CAP_DSHOW)  # Changed to use default camera
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 2560)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1440)
        print("Initialized Webcam")

        # Set up the GUI
        self.setup_gui()
        print("GUI Setup Complete")

        # Start video capture
        self.capture_thread = threading.Thread(target=self.capture_webcam)
        self.capture_thread.daemon = True
        self.capture_thread.start()

        # Start the image processing thread
        self.processing_thread = threading.Thread(target=self.process_output)
        self.processing_thread.daemon = True
        self.processing_thread.start()

        # Release the video capture when the app closes
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def setup_gui(self):
        # Configure grid layout
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_columnconfigure(1, weight=3)

        # Create frames
        self.left_frame = tk.Frame(self.root, bg="azure2")
        self.left_frame.grid(row=0, column=0, sticky="nswe")
        self.left_frame.pack_propagate(False)

        self.right_frame = tk.Frame(self.root, bg="azure2")
        self.right_frame.grid(row=0, column=1, sticky="nswe")
        self.right_frame.pack_propagate(False)

        # ---------------------------------------------------------------------------- #
        #                                  Left Frame                                  #
        # ---------------------------------------------------------------------------- #

        # ---------------------------------- Cameras --------------------------------- #
        self.input_label = tk.Label(self.left_frame, text="Input Image", bg="azure1")
        self.input_label.pack(fill="x", padx=5, pady=(5, 0))
        self.input_canvas = tk.Canvas(self.left_frame)
        self.input_canvas.pack(fill="x", padx=5, pady=(5, 0))

        self.reference_label = tk.Label(
            self.left_frame, text="Reference Image", bg="azure1"
        )
        self.reference_label.pack(fill="x", padx=5, pady=(5, 0))
        self.reference_canvas = tk.Canvas(self.left_frame)
        self.reference_canvas.pack(fill="x", padx=5, pady=(5, 0))

        # ---------------------------------- Buttons --------------------------------- #
        self.button_frame = tk.Frame(self.left_frame)
        self.button_frame.pack(fill="x", padx=5, pady=(5, 0))

        self.capture_reference_button = tk.Button(
            self.button_frame,
            text="Capture Reference",
            command=self.capture_reference,
            bg="azure1",
        )
        self.capture_reference_button.pack(side=tk.LEFT, expand=True, fill="both")

        self.clear_reference_button = tk.Button(
            self.button_frame,
            text="Clear Reference",
            command=self.clear_reference,
            bg="azure1",
        )
        self.clear_reference_button.pack(side=tk.LEFT, expand=True, fill="both")

        self.upload_file_button = tk.Button(
            self.button_frame,
            text="Upload Reference",
            command=self.upload_reference,
            bg="azure1",
        )
        self.upload_file_button.pack(side=tk.LEFT, expand=True, fill="both")

        # ---------------------------- Mode Radio Buttons ---------------------------- #
        self.mode_label = tk.Label(self.left_frame, text="Output Mode", bg="azure1")
        self.mode_label.pack(fill="x", padx=5, pady=(5, 0))
        self.mode = tk.StringVar(value="none")
        modes = (
            ("None", "none"),
            ("Overlay", "overlay"),
            ("Difference", "difference"),
            ("SSIM", "ssim"),
            ("Flicker", "flicker"),
            ("ChangeChip", "changechip"),
        )

        for mode_text, mode_value in modes:
            r = tk.Radiobutton(
                self.left_frame,
                text=mode_text,
                value=mode_value,
                variable=self.mode,
                bg="azure1",
            )
            r.pack(fill="x", padx=5)

        # ------------------------- Preprocessing Checkboxes ------------------------- #
        self.preprocess_label = tk.Label(
            self.left_frame, text="Preprocessing Options", bg="azure1"
        )
        self.preprocess_label.pack(fill="x", padx=5, pady=(5, 0))

        self.homography_var = tk.IntVar()
        self.histogram_var = tk.IntVar()

        self.homography_checkbutton = tk.Checkbutton(
            self.left_frame,
            text="Homography",
            variable=self.homography_var,
            onvalue=1,
            offvalue=0,
            bg="azure1",
        )
        self.homography_checkbutton.pack(fill="x", padx=5, pady=(5, 0))

        self.histogram_checkbutton = tk.Checkbutton(
            self.left_frame,
            text="Histogram Matching",
            variable=self.histogram_var,
            onvalue=1,
            offvalue=0,
            bg="azure1",
        )
        self.histogram_checkbutton.pack(fill="x", padx=5, pady=(5, 0))

        # ---------------------------------------------------------------------------- #
        #                                  Right Frame                                 #
        # ---------------------------------------------------------------------------- #
        self.output_label = tk.Label(self.right_frame, text="Output Image", bg="azure1")
        self.output_label.pack(fill="x", padx=5, pady=(5, 0))
        self.output_canvas = PanZoomCanvas(master=self.right_frame)

        self.root.bind("<Configure>", self.resize_all_canvases)
        self.resize_all_canvases(None)

        # Schedule the initial display update
        self.root.after(10, self.update_display)

    def capture_webcam(self):
        while self.cap.isOpened():
            ret, frame = self.cap.read()
            if ret:
                self.current_frame = frame
                # Only put the latest frame into the queue, discard the old one if the queue is full
                if not self.frame_queue.full():
                    self.frame_queue.put(frame)
                else:
                    try:
                        self.frame_queue.get_nowait()
                    except queue.Empty:
                        pass
                    self.frame_queue.put(frame)

    def resize_all_canvases(self, event):
        def resize_canvas(canvas, frame):
            # Calculate canvas dimensions with 16:9 aspect ratio
            frame_width = frame.winfo_width() - 10  # Adjust for padding
            canvas_width = frame_width
            canvas_height = int(canvas_width * 9 / 16)
            canvas.config(width=canvas_width, height=canvas_height)

        # Resize each canvas
        resize_canvas(self.input_canvas, self.left_frame)
        resize_canvas(self.reference_canvas, self.left_frame)

    # ------------------------- Display Update Functions ------------------------- #

    def update_display(self):
        if self.current_frame is not None:
            try:
                self.update_input_display()
                self.update_reference_display()
                self.update_output_display()
            except Exception as e:
                print(f"Error updating display: {e}")

        # Schedule the next display update
        self.root.after(10, self.update_display)

    def update_input_display(self):
        canvas_size = (
            self.input_canvas.winfo_width(),
            self.input_canvas.winfo_height(),
        )
        converted_frame = self.convert_frame_format(self.current_frame, canvas_size)

        self.input_canvas.create_image(0, 0, anchor=tk.NW, image=converted_frame)
        self.input_canvas.image = converted_frame

    def update_reference_display(self):
        canvas_size = (
            self.reference_canvas.winfo_width(),
            self.reference_canvas.winfo_height(),
        )
        image_source = (
            self.current_frame if self.reference_image is None else self.reference_image
        )

        converted_frame = self.convert_frame_format(image_source, canvas_size)
        self.reference_canvas.create_image(0, 0, anchor=tk.NW, image=converted_frame)
        self.reference_canvas.image = converted_frame

    def update_output_display(self):
        if self.reference_image is not None:
            self.output_canvas.set_image(
                self.convert_frame_format(self.processed_frame, convert_to_tk=False)
            )
        else:
            self.output_canvas.remove_image()

    # ------------------------- Image Processing Functions ------------------------- #

    def process_output(self):
        while True:
            if self.reference_image is None:
                self.processed_frame = None
                continue
            frame = self.frame_queue.get()  # Wait for a frame to be available
            if frame is not None:
                try:
                    self.processed_frame = self.process_current_frame(frame)
                except Exception as e:
                    print(f"Error processing output frame: {e}")

    def process_current_frame(self, frame):
        # Retrieve and store the state of variables
        histogram_active = self.histogram_var.get() == 1
        homography_active = self.homography_var.get() == 1
        mode = self.mode.get()

        # Apply pre-processing steps
        if histogram_active:
            frame = self.match_colors(self.reference_image, frame)

        if homography_active:
            frame = self.apply_homography(self.reference_image, frame)

        # Define a dictionary to map modes to their corresponding functions
        mode_functions = {
            "overlay": self.process_overlay,
            "difference": self.process_difference,
            "ssim": self.process_ssim,
            "flicker": self.process_flicker,
            "changechip": self.process_changechip,
        }

        # Apply the selected mode function
        output = mode_functions.get(mode, lambda ref, frm: frm)(
            self.reference_image, frame
        )

        return output

    def process_overlay(self, reference_image, current_frame):
        alpha = 0.5
        return cv2.addWeighted(reference_image, alpha, current_frame, 1 - alpha, 0)

    def process_difference(self, reference_image, current_frame, min_contour_area=400):
        diff = cv2.absdiff(reference_image, current_frame)
        gray_diff = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)

        # Threshold the grayscale difference image to create a binary mask
        _, binary_mask = cv2.threshold(gray_diff, 50, 255, cv2.THRESH_BINARY)

        # Find contours in the binary mask
        contours, _ = cv2.findContours(
            binary_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        # Filter and draw the contours filled with red on the current frame
        red_diff = current_frame.copy()
        for contour in contours:
            if cv2.contourArea(contour) >= min_contour_area:
                cv2.drawContours(
                    red_diff, [contour], -1, (0, 0, 255), thickness=cv2.FILLED
                )

        return red_diff

    def process_ssim(self, reference_image, current_frame):
        gray_reference = cv2.cvtColor(reference_image, cv2.COLOR_BGR2GRAY)
        gray_frame = cv2.cvtColor(current_frame, cv2.COLOR_BGR2GRAY)
        _, diff = structural_similarity(gray_reference, gray_frame, full=True)
        diff = (diff * 255).astype("uint8")
        diff_color = cv2.cvtColor(diff, cv2.COLOR_GRAY2BGR)
        return diff_color

    def process_flicker(self, reference_image, frame, delay=0.2):
        time.sleep(delay)
        self.flicker_state = not self.flicker_state
        return reference_image if self.flicker_state else frame

    def process_changechip(self, reference_image, frame):
        output = pipeline((frame, reference_image), resize_factor=0.5)
        return output

    # ------------------------- Feature-Based Homography ------------------------- #

    def apply_homography(self, reference_image, current_frame):
        # Convert images to grayscale
        gray_reference = cv2.cvtColor(reference_image, cv2.COLOR_BGR2GRAY)
        gray_frame = cv2.cvtColor(current_frame, cv2.COLOR_BGR2GRAY)

        # Detect ORB keypoints and descriptors
        orb = cv2.ORB_create()
        keypoints1, descriptors1 = orb.detectAndCompute(gray_reference, None)
        keypoints2, descriptors2 = orb.detectAndCompute(gray_frame, None)

        # Match descriptors
        bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        matches = bf.match(descriptors1, descriptors2)
        matches = sorted(matches, key=lambda x: x.distance)

        # Extract location of good matches
        points1 = np.zeros((len(matches), 2), dtype=np.float32)
        points2 = np.zeros((len(matches), 2), dtype=np.float32)

        for i, match in enumerate(matches):
            points1[i, :] = keypoints1[match.queryIdx].pt
            points2[i, :] = keypoints2[match.trainIdx].pt

        # Find homography
        h, mask = cv2.findHomography(points2, points1, cv2.RANSAC)

        # Use homography to warp current frame
        height, width, channels = reference_image.shape
        aligned_frame = cv2.warpPerspective(current_frame, h, (width, height))

        return aligned_frame

    def match_colors(self, reference_image, current_frame):
        return match_histograms(current_frame, reference_image, channel_axis=-1)

    # ----------------------------- Button Functions ----------------------------- #

    def capture_reference(self):
        self.reference_image = self.current_frame.copy()

    def clear_reference(self):
        self.reference_image = None

    def upload_reference(self):
        file_path = filedialog.askopenfilename()
        if file_path:
            self.reference_image = cv2.imread(file_path)

    # ------------------------------ Other Functions ----------------------------- #

    def convert_frame_format(self, frame, target_size=None, convert_to_tk=True):
        frame = cv2.resize(frame, target_size) if target_size else frame
        pil_image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).rotate(180)
        return ImageTk.PhotoImage(pil_image) if convert_to_tk else pil_image

    def on_closing(self):
        self.cap.release()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = PCBQualityAssuranceApp(root)
    root.mainloop()
