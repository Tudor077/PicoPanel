"""The page rotation, as cards you can pick up.

A row used to be the word "GAME". A word is a poor way to choose between
fourteen screens, so a row is now a picture OF that screen - the board's own
frame, collected by walking the pages and photographing each one. A page you
recognise is a page you can order.

The pictures are real. A mock-up would be a second drawing of every page, and
the moment the firmware changed one it would start lying.
"""

import base64
import io
import tkinter as tk
from tkinter import ttk

SHOT_SCALE = 2
SHOT_W, SHOT_H = 128 * SHOT_SCALE, 32 * SHOT_SCALE

# How long to let the board settle on a page before photographing it. A frame
# takes about 18 ms, so this is a few frames - enough that what we catch is the
# new page and not the tail of the old one.
SETTLE_MS = 130


def frame_photo(w, h, data, scale=SHOT_SCALE):
    """An SSD1306 frame buffer as a Tk image.

    The buffer is in pages: one byte holds eight pixels stacked vertically, so
    pixel (x, y) is bit y%8 of byte x + (y//8)*width.
    """
    need = w * h // 8
    if len(data) < need:
        return None
    rows = []
    for y in range(h):
        base, bit = (y >> 3) * w, 1 << (y & 7)
        rows.append("{" + " ".join(
            "#d8f4ff" if data[base + x] & bit else "#0a0c0e"
            for x in range(w)) + "}")
    img = tk.PhotoImage(width=w, height=h)
    img.put(" ".join(rows))
    return img.zoom(scale)


class PageList(ttk.Frame):
    """Cards for the rotation, and cards for what is left out of it."""

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.shots = {}             # page number -> PhotoImage
        self._rows = {}
        self._drag_from = None
        self._build()

    def _build(self):
        head = ttk.Frame(self)
        head.pack(fill="x")
        ttk.Label(head, wraplength=430, justify="left",
                  text="USER walks these, in this order. Drag a card by its "
                       "handle to move it. Double-click one of your own pages "
                       "to lay it out."
                  ).pack(side="left", anchor="w")
        ttk.Button(head, text="Refresh pictures",
                   command=self.collect).pack(side="right", padx=4)

        cols = ttk.Frame(self)
        cols.pack(fill="both", expand=True, pady=6)

        self.inbox = ttk.LabelFrame(cols, text="In the rotation")
        self.inbox.pack(side="left", fill="both", expand=True)
        self.outbox = ttk.LabelFrame(cols, text="Not shown")
        self.outbox.pack(side="left", fill="both", expand=True, padx=(8, 0))

    # ------------------------------------------------------------- filling
    def refill(self):
        for box in (self.inbox, self.outbox):
            for child in box.winfo_children():
                child.destroy()
        self._rows = {}
        order, rest = self.app._pg_ids
        for i, pg in enumerate(order):
            self._card(self.inbox, pg, i, True)
        for pg in rest:
            self._card(self.outbox, pg, None, False)

    def _card(self, parent, pg, idx, inside):
        name = self.app.page_name(pg)
        card = tk.Frame(parent, bd=1, relief="solid", bg="#20242c")
        card.pack(fill="x", padx=4, pady=3)
        self._rows[pg] = card

        # The handle. Dragging anywhere on the card would fight with the
        # double-click that opens the editor, so the grip is its own column -
        # the same reason a list on a phone has one.
        grip = tk.Label(card, text="⠇\n⠇", bg="#20242c", fg="#8b93a1",
                        cursor="fleur", font=("", 9))
        grip.pack(side="left", padx=(4, 2))

        shot = self.shots.get(pg)
        pic = tk.Label(card, image=shot, bg="#0a0c0e", bd=0,
                       width=SHOT_W if shot is None else 0,
                       height=SHOT_H if shot is None else 0)
        pic.pack(side="left", padx=4, pady=4)

        side = tk.Frame(card, bg="#20242c")
        side.pack(side="left", fill="y", padx=6)
        tk.Label(side, text=name, bg="#20242c", fg="#e6e8ec",
                 font=("", 10, "bold")).pack(anchor="w")
        if self.app.slot_of(pg) is not None:
            tk.Label(side, text="yours - double-click to lay out", bg="#20242c",
                     fg="#8b93a1", font=("", 8)).pack(anchor="w")

        btn = tk.Frame(card, bg="#20242c")
        btn.pack(side="right", padx=6)
        tk.Button(btn, text="→" if inside else "←", width=2, bd=0,
                  bg="#2b3038", fg="#e6e8ec",
                  command=lambda p=pg: self.app.pg_toggle(p)).pack(side="right", padx=2)

        for wdg in (card, pic, side):
            wdg.bind("<Button-1>", lambda _e, p=pg: self.app.pg_show(p))
            wdg.bind("<Double-Button-1>", lambda _e, p=pg: self.app.pg_open(p))
        if inside:
            grip.bind("<ButtonPress-1>", lambda e, i=idx: self._grab(i))
            grip.bind("<B1-Motion>", self._haul)
            grip.bind("<ButtonRelease-1>", self._release)

    # -------------------------------------------------------------- moving
    def _grab(self, i):
        self._drag_from = i

    def _haul(self, ev):
        if self._drag_from is None:
            return
        order, _rest = self.app._pg_ids
        # Which card is the pointer over? Measured against the cards' own
        # positions, so it works whatever their height turns out to be.
        y = ev.y_root
        for j, pg in enumerate(order):
            card = self._rows.get(pg)
            if card is None or not card.winfo_ismapped():
                continue
            top = card.winfo_rooty()
            if top <= y < top + card.winfo_height():
                if j != self._drag_from:
                    order.insert(j, order.pop(self._drag_from))
                    self._drag_from = j
                    self.refill()
                return

    def _release(self, _ev):
        if self._drag_from is not None:
            self.app.pg_apply()
        self._drag_from = None

    # ---------------------------------------------------------- collecting
    def collect(self):
        """Walk every page and photograph it, then put the panel back.

        Takes about three seconds for fourteen pages. Done on demand rather
        than at startup: it moves the panel about, and doing that behind
        somebody's back the moment the app opens would be rude.
        """
        self.app.collect_shots(self._shot_done)

    def _shot_done(self, pg, photo):
        if photo is not None:
            self.shots[pg] = photo
        self.refill()
