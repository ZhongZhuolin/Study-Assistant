"""
Vision Study Assistant — Improved
- Uses Claude claude-sonnet-4-20250514 (more accurate for STEM)
- Streams response for speed
- Rich formatted result window (color-coded Q/Method/Answer)
- Hotkey: CTRL+SHIFT+S  |  Quit: ESC
"""

import os
import base64
import threading
from datetime import datetime

import mss
from PIL import Image, ImageTk

import tkinter as tk
from tkinter import font as tkfont

import keyboard
import anthropic


# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

CAPTURE_HOTKEY   = "ctrl+shift+s"
OUTPUT_DIR       = "captures"
MODEL            = "claude-sonnet-4-20250514"
MAX_THUMB_SIZE   = (1120, 1120)   # px — keeps quality while staying fast
MAX_TOKENS       = 400

capture_in_progress = False

os.makedirs(OUTPUT_DIR, exist_ok=True)

client = anthropic.Anthropic(api_key = "")          # put your api key here


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def timestamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def encode_image(path: str) -> tuple[str, str]:
    """Return (base64_data, media_type)."""
    with open(path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode(), "image/png"


def prepare_image(path: str) -> str:
    """Resize to MAX_THUMB_SIZE, save as separate file, return new path."""
    img = Image.open(path).convert("RGB")
    img.thumbnail(MAX_THUMB_SIZE, Image.LANCZOS)
    out = path.replace(".png", "_sm.png")
    img.save(out, optimize=True)
    return out


# ─────────────────────────────────────────────
# SCREENSHOT / CROP
# ─────────────────────────────────────────────

def capture_full_screen() -> str:
    path = os.path.join(OUTPUT_DIR, f"full_{timestamp()}.png")
    with mss.mss() as sct:
        shot = sct.grab(sct.monitors[1])
        Image.frombytes("RGB", shot.size, shot.rgb).save(path)
    return path


def crop_image(path: str, x1, y1, x2, y2) -> str:
    out = os.path.join(OUTPUT_DIR, f"crop_{timestamp()}.png")
    img = Image.open(path)
    img.crop((min(x1,x2), min(y1,y2), max(x1,x2), max(y1,y2))).save(out)
    return out


# ─────────────────────────────────────────────
# VISION  (Claude, streaming)
# ─────────────────────────────────────────────

PROMPT = """\
You are a precise academic tutor. Analyze the screenshot and solve every
question you find. NOTE: Any given values or context shown anywhere in the
image (left panels, sidebars, problem setup boxes) are part of the problem
and must be used in your solution.

For EACH question use EXACTLY this format:
QUESTION: <question text>
METHOD: <3-5 words>
ANSWER: <final answer only>

Be concise. No extra explanation.
"""



def analyze_image_streaming(image_path: str, on_chunk, on_done):
    """
    Streams Claude's response.
    on_chunk(text) is called for each streamed token.
    on_done() is called when streaming finishes.
    """
    sm = prepare_image(image_path)
    b64, media_type = encode_image(sm)

    def run():
        try:
            with client.messages.stream(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": PROMPT},
                    ],
                }],
            ) as stream:
                for text in stream.text_stream:
                    on_chunk(text)
        except Exception as e:
            on_chunk(f"\n\n[Error: {e}]")
        finally:
            on_done()

    threading.Thread(target=run, daemon=True).start()


# ─────────────────────────────────────────────
# REGION SELECTOR
# ─────────────────────────────────────────────

class RegionSelector:
    def __init__(self, image_path: str):
        self.root = tk.Tk()
        self.root.title("Drag to select question area — release to confirm")
        self.root.attributes("-topmost", True)

        img = Image.open(image_path)
        img.thumbnail((1600, 900))
        self.tkimg = ImageTk.PhotoImage(img)
        self.scale_x = Image.open(image_path).width  / img.width
        self.scale_y = Image.open(image_path).height / img.height

        self.canvas = tk.Canvas(self.root, width=img.width, height=img.height,
                                cursor="crosshair", bg="#000")
        self.canvas.pack()
        self.canvas.create_image(0, 0, anchor="nw", image=self.tkimg)

        self.start = None
        self.rect  = None
        self.coords = None

        self.canvas.bind("<ButtonPress-1>",   self._press)
        self.canvas.bind("<B1-Motion>",       self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.root.bind("<Escape>",            lambda _: self.root.destroy())

    def _press(self, e):
        self.start = (e.x, e.y)
        if self.rect:
            self.canvas.delete(self.rect)
        self.rect = self.canvas.create_rectangle(
            e.x, e.y, e.x, e.y, outline="#FF3B30", width=2)

    def _drag(self, e):
        self.canvas.coords(self.rect, self.start[0], self.start[1], e.x, e.y)

    def _release(self, e):
        x1, y1 = self.start
        x2, y2 = e.x, e.y
        # map back to original image coords
        self.coords = (
            int(x1 * self.scale_x), int(y1 * self.scale_y),
            int(x2 * self.scale_x), int(y2 * self.scale_y),
        )
        self.root.destroy()

    def get(self):
        self.root.mainloop()
        return self.coords


# ─────────────────────────────────────────────
# RESULT WINDOW  (formatted, streaming)
# ─────────────────────────────────────────────

DARK_BG   = "#1A1A2E"
PANEL_BG  = "#16213E"
TEXT_FG   = "#E0E0E0"
Q_COLOR   = "#4FC3F7"   # light blue  — QUESTION
M_COLOR   = "#FFD54F"   # amber       — METHOD
A_COLOR   = "#69F0AE"   # green       — ANSWER
S_COLOR   = "#CE93D8"   # purple      — STEPS
DIV_COLOR = "#3A3A5C"

TAG_MAP = {
    "QUESTION:": ("q_tag",  Q_COLOR),
    "METHOD:":   ("m_tag",  M_COLOR),
    "ANSWER:":   ("a_tag",  A_COLOR),
    "STEPS:":    ("s_tag",  S_COLOR),
}


class ResultWindow:
    def __init__(self, image_path: str):
        self.root = tk.Tk()
        self.root.title("Study Assistant")
        self.root.geometry("820x580")
        self.root.configure(bg=DARK_BG)
        self.root.attributes("-topmost", True)

        # ── header ──
        hdr = tk.Frame(self.root, bg=PANEL_BG, pady=8)
        hdr.pack(fill="x")
        tk.Label(hdr, text="📚  Study Assistant", bg=PANEL_BG,
                 fg=TEXT_FG, font=("Courier New", 14, "bold")).pack(side="left", padx=14)
        self.status = tk.Label(hdr, text="⏳ Analyzing…", bg=PANEL_BG,
                               fg=M_COLOR, font=("Courier New", 10))
        self.status.pack(side="right", padx=14)

        # ── text area ──
        mono = tkfont.Font(family="Courier New", size=11)
        self.txt = tk.Text(
            self.root, bg=PANEL_BG, fg=TEXT_FG,
            font=mono, wrap="word",
            relief="flat", bd=0,
            padx=16, pady=12,
            selectbackground="#2A4A7A",
        )
        sb = tk.Scrollbar(self.root, command=self.txt.yview, bg=DARK_BG)
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt.pack(fill="both", expand=True, padx=(8,0), pady=(4,0))

        # configure colour tags
        self.txt.tag_configure("q_tag",  foreground=Q_COLOR, font=("Courier New", 11, "bold"))
        self.txt.tag_configure("m_tag",  foreground=M_COLOR, font=("Courier New", 11, "bold"))
        self.txt.tag_configure("a_tag",  foreground=A_COLOR, font=("Courier New", 11, "bold"))
        self.txt.tag_configure("s_tag",  foreground=S_COLOR, font=("Courier New", 11, "bold"))
        self.txt.tag_configure("div",    foreground=DIV_COLOR)
        self.txt.tag_configure("plain",  foreground=TEXT_FG)

        # ── footer ──
        ft = tk.Frame(self.root, bg=DARK_BG, pady=6)
        ft.pack(fill="x")
        tk.Button(ft, text="Copy", command=self._copy,
                  bg="#2A4A7A", fg=TEXT_FG, relief="flat",
                  padx=12, font=("Courier New", 10)).pack(side="left", padx=10)
        tk.Button(ft, text="Close  [ESC]", command=self.root.destroy,
                  bg="#3A1A2E", fg=TEXT_FG, relief="flat",
                  padx=12, font=("Courier New", 10)).pack(side="right", padx=10)
        self.root.bind("<Escape>", lambda _: self.root.destroy())

        # buffer for partial-line colouring
        self._buf = ""

        # kick off streaming
        analyze_image_streaming(image_path, self._on_chunk, self._on_done)

    # ── streaming callbacks ──────────────────

    def _on_chunk(self, text: str):
        self.root.after(0, self._insert, text)

    def _on_done(self):
        self.root.after(0, lambda: self.status.configure(
            text="✅ Done", fg=A_COLOR))

    def _insert(self, text: str):
        self._buf += text
        # flush whole lines so we can colour keywords
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._write_line(line + "\n")
        # also flush if no newline but we have data (for live feel)
        if self._buf:
            self._write_line(self._buf, flush=False)
            self._buf = ""
        self.txt.see("end")

    def _write_line(self, line: str, flush=True):
        stripped = line.strip()
        for keyword, (tag, _) in TAG_MAP.items():
            if stripped.startswith(keyword):
                # keyword in colour, rest in plain
                kw_end = line.index(keyword) + len(keyword)
                self.txt.insert("end", line[:kw_end], tag)
                self.txt.insert("end", line[kw_end:], "plain")
                return
        if set(stripped) <= set("─━─ \t"):   # divider lines
            self.txt.insert("end", line, "div")
        else:
            self.txt.insert("end", line, "plain")

    # ── utils ────────────────────────────────

    def _copy(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.txt.get("1.0", "end"))

    def show(self):
        self.root.mainloop()


# ─────────────────────────────────────────────
# MAIN FLOW
# ─────────────────────────────────────────────

def capture_flow():
    global capture_in_progress
    if capture_in_progress:
        return
    capture_in_progress = True
    try:
        full   = capture_full_screen()
        coords = RegionSelector(full).get()
        if not coords:
            return
        crop = crop_image(full, *coords)
        ResultWindow(crop).show()
    finally:
        capture_in_progress = False


def on_hotkey():
    threading.Thread(target=capture_flow, daemon=True).start()


def main():
    print("╔══════════════════════════════════╗")
    print("║   Vision Study Assistant v2      ║")
    print("║   CTRL+SHIFT+S  →  capture       ║")
    print("║   ESC           →  quit          ║")
    print("╚══════════════════════════════════╝")

    keyboard.add_hotkey(CAPTURE_HOTKEY, on_hotkey)
    keyboard.wait("esc")


if __name__ == "__main__":
    main()