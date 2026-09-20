"""The page editor: drag widgets onto a 128x32 canvas and watch them live.

The preview is not a drawing of what the panel will show - it IS what the panel
will show. The same `widgets.render` produces the image here and the 512 bytes
that go down the wire, so the two cannot disagree.

Frames are sent only while the board is actually on that page. It says so
itself: `!PAGE n` whenever the page changes. Streaming ten kilobytes a second at
a page nobody is looking at would be silly, and guessing from the status line
would not work at all when reporting is switched off.
"""

import base64
import io
import json
import tkinter as tk
from tkinter import ttk

import widgets as WG

ZOOM = 4
CW, CH = WG.W * ZOOM, WG.H * ZOOM

# Fast enough that a needle looks alive, slow enough to leave the link alone:
# a frame is 684 characters of base64, so this is about 10 kB a second.
SEND_HZ = 15


def _photo(img):
    """A PIL image as a Tk PhotoImage, without needing PIL's Tk bridge.

    Tk reads PPM out of a base64 blob, and PIL writes PPM. That is the whole
    trick, and it saves a dependency that is missing often enough to matter.
    """
    buf = io.BytesIO()
    img.convert("RGB").resize((CW, CH), 0).save(buf, format="PPM")
    return tk.PhotoImage(data=base64.b64encode(buf.getvalue()))


class Editor(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.items = [WG.Widget.from_dict(d)
                      for d in (app.cfg.get("custom_page") or [])]
        self.sel = None
        self._drag = None
        self._photo_ref = None
        self._build()
        self._tick()

    # ------------------------------------------------------------ building
    def _build(self):
        pal = ttk.Frame(self)
        pal.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Label(pal, text="Drop one on:").pack(side="left", padx=(0, 6))
        for kind, label, defaults in WG.PALETTE:
            ttk.Button(pal, text=label, width=9,
                       command=lambda k=kind, d=defaults: self._add(k, d)
                       ).pack(side="left", padx=2)
        ttk.Button(pal, text="Delete", width=8,
                   command=self._delete).pack(side="right", padx=2)

        self.canvas = tk.Canvas(self, width=CW, height=CH, bg="#0a0c0e",
                                highlightthickness=1, highlightbackground="#555")
        self.canvas.pack(padx=8, pady=4)
        self.canvas.bind("<Button-1>", self._down)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", self._up)

        ttk.Label(self, foreground="#555", wraplength=520, justify="left",
                  text="Drag to place. This preview is the picture the panel "
                       "gets, drawn by the same code - it cannot drift. The "
                       "page is sent only while the board is showing it."
                  ).pack(anchor="w", padx=8)

        props = ttk.LabelFrame(self, text="Selected widget")
        props.pack(fill="x", padx=8, pady=6)
        row = ttk.Frame(props)
        row.pack(fill="x", padx=6, pady=4)

        ttk.Label(row, text="Shows").pack(side="left")
        self.f_var = tk.StringVar()
        self.f_box = ttk.Combobox(row, textvariable=self.f_var, width=16,
                                  state="readonly",
                                  values=[lbl for _k, lbl in WG.FIELDS])
        self.f_box.pack(side="left", padx=4)
        self.f_box.bind("<<ComboboxSelected>>", lambda _e: self._edit())

        ttk.Label(row, text="Label").pack(side="left", padx=(10, 0))
        self.l_var = tk.StringVar()
        e = ttk.Entry(row, textvariable=self.l_var, width=10)
        e.pack(side="left", padx=4)
        e.bind("<KeyRelease>", lambda _e: self._edit())

        self.spins = {}
        for name, lo, hi in (("size", 6, 30), ("w", 1, WG.W), ("h", 1, WG.H)):
            ttk.Label(row, text=name).pack(side="left", padx=(10, 0))
            v = tk.IntVar()
            s = ttk.Spinbox(row, from_=lo, to=hi, width=4, textvariable=v,
                            command=self._edit)
            s.pack(side="left", padx=2)
            s.bind("<KeyRelease>", lambda _e: self._edit())
            self.spins[name] = v

    # ------------------------------------------------------------- editing
    def _add(self, kind, defaults):
        w = WG.Widget(kind=kind, x=4, y=4, **defaults)
        self.items.append(w)
        self.sel = w
        self._show_props()
        self._save()

    def _delete(self):
        if self.sel in self.items:
            self.items.remove(self.sel)
            self.sel = None
            self._save()

    def _hit(self, x, y):
        """Topmost widget under the point, so the thing you see on top is the
        thing you grab."""
        for w in reversed(self.items):
            if w.x <= x < w.x + max(w.w, 4) and w.y <= y < w.y + max(w.h, 4):
                return w
        return None

    def _down(self, ev):
        x, y = ev.x // ZOOM, ev.y // ZOOM
        w = self._hit(x, y)
        self.sel = w
        self._drag = (x - w.x, y - w.y) if w else None
        self._show_props()

    def _move(self, ev):
        if not self.sel or self._drag is None:
            return
        dx, dy = self._drag
        self.sel.x = max(0, min(WG.W - 1, ev.x // ZOOM - dx))
        self.sel.y = max(0, min(WG.H - 1, ev.y // ZOOM - dy))

    def _up(self, _ev):
        if self._drag is not None:
            self._save()
        self._drag = None

    def _show_props(self):
        w = self.sel
        if not w:
            return
        self.f_var.set(WG.FIELD_LABEL.get(w.field, w.field))
        self.l_var.set(w.label)
        self.spins["size"].set(w.size)
        self.spins["w"].set(w.w)
        self.spins["h"].set(w.h)

    def _edit(self):
        w = self.sel
        if not w:
            return
        want = self.f_var.get()
        for key, lbl in WG.FIELDS:
            if lbl == want:
                w.field = key
                break
        w.label = self.l_var.get()
        try:
            w.size = int(self.spins["size"].get())
            w.w = int(self.spins["w"].get())
            w.h = int(self.spins["h"].get())
        except Exception:
            pass                      # half-typed number: leave it until it's whole
        self._save()

    def _save(self):
        self.app.cfg["custom_page"] = [w.to_dict() for w in self.items]
        self.app.save_cfg()

    # ------------------------------------------------------------ painting
    def _tick(self):
        try:
            img = WG.render(self.items, self.app.widget_data())
            if img is not None:
                self._photo_ref = _photo(img)
                self.canvas.delete("all")
                self.canvas.create_image(0, 0, anchor="nw", image=self._photo_ref)
                if self.sel:
                    w = self.sel
                    self.canvas.create_rectangle(
                        w.x * ZOOM, w.y * ZOOM,
                        (w.x + w.w) * ZOOM - 1, (w.y + w.h) * ZOOM - 1,
                        outline="#e8744f", dash=(3, 2))
        except Exception:
            pass                      # the editor must never take the app down
        self.after(100, self._tick)

    # -------------------------------------------------------------- output
    def frame_bytes(self):
        """The 512 bytes for the board, or None."""
        img = WG.render(self.items, self.app.widget_data())
        if img is None:
            return None
        return WG.to_frame(img)
