"""The page rotation, as cards you can pick up.

A row used to be the word "GAME". A word is a poor way to choose between
eighteen screens, so a row is now a picture OF that screen - the board's own
frame, collected by walking the pages and photographing each one. A page you
recognise is a page you can order.

The pictures are real. A mock-up would be a second drawing of every page, and
the moment the firmware changed one it would start lying.

One list, not two. The pages USER walks are at the top, the ones you have put
aside sit below the line, and a card crosses that line without moving house.
Two columns cost twice the width, and a card is 256 pixels of picture before
anything else.
"""

import tkinter as tk
from tkinter import ttk

SHOT_SCALE = 2
SHOT_W, SHOT_H = 128 * SHOT_SCALE, 32 * SHOT_SCALE

# How long to let the board settle on a page before photographing it. A frame
# takes about 18 ms, so this is a few frames - enough that what we catch is the
# new page and not the tail of the old one.
SETTLE_MS = 130

BG = "#1b1e24"
CARD = "#20242c"
DIM = "#8b93a1"


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
    """The cards: what USER walks, and what has been left out of it."""

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.shots = {}             # page number -> PhotoImage
        self._blank = None
        self._rows = {}
        self._drag_from = None
        self._build()

    def _build(self):
        head = ttk.Frame(self)
        head.pack(fill="x")
        ttk.Label(head, wraplength=330, justify="left",
                  text="USER walks these, in this order. Drag a card by its "
                       "handle to move it. Double-click one of your own pages "
                       "to lay it out. + makes a new one right below."
                  ).pack(side="left", anchor="w")
        ttk.Button(head, text="Refresh pictures",
                   command=self.collect).pack(side="right", padx=4)
        ttk.Button(head, text="+ New page",
                   command=lambda: self.app.pg_new()).pack(side="right", padx=4)

        # ---- the scrolling list. Eighteen cards are far taller than the
        # window, and a list you cannot reach the bottom of is not a list you
        # can put in order.
        box = ttk.Frame(self)
        box.pack(fill="both", expand=True, pady=6)
        self.canvas = tk.Canvas(box, highlightthickness=0, bg=BG, width=520)
        bar = ttk.Scrollbar(box, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.list = tk.Frame(self.canvas, bg=BG)
        win = self.canvas.create_window((0, 0), window=self.list, anchor="nw")
        self.list.bind("<Configure>", lambda _e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure(win, width=e.width))
        self._wheel(self.canvas)
        self._wheel(self.list)

    def _wheel(self, wdg):
        """The wheel belongs to the list, not to whatever card happens to be
        under the pointer - Tk hands it to the innermost widget."""
        wdg.bind("<MouseWheel>",
                 lambda e: self.canvas.yview_scroll(-e.delta // 120, "units"))

    def _blank_shot(self):
        """A dark rectangle the size of the panel, for a page not yet seen.

        A Label with no image at all sizes itself in CHARACTERS and LINES, so
        128x32 meant as pixels came out 256 characters wide and 64 lines tall:
        one card filling the whole window and hiding every other one.
        """
        if self._blank is None:
            img = tk.PhotoImage(width=SHOT_W, height=SHOT_H)
            img.put("#0a0c0e", to=(0, 0, SHOT_W, SHOT_H))
            self._blank = img
        return self._blank

    # ------------------------------------------------------------- filling
    def refill(self):
        for child in self.list.winfo_children():
            child.destroy()
        self._rows = {}
        order, rest = self.app._pg_ids
        for i, pg in enumerate(order):
            self._card(pg, i, True)
        if rest:
            head = tk.Frame(self.list, bg=BG)
            head.pack(fill="x", pady=(10, 2))
            tk.Label(head, text="Not shown - USER walks past these", bg=BG,
                     fg=DIM, font=("", 9)).pack(side="left", padx=8)
            self._wheel(head)
        for pg in rest:
            self._card(pg, None, False)

    def _card(self, pg, idx, inside):
        name = self.app.page_name(pg)
        card = tk.Frame(self.list, bd=1, relief="solid", bg=CARD)
        card.pack(fill="x", padx=4, pady=3)
        self._rows[pg] = card

        # The handle. Dragging anywhere on the card would fight with the
        # double-click that opens the editor, so the grip is its own column -
        # the same reason a list on a phone has one.
        grip = tk.Label(card, text="⠇\n⠇", bg=CARD,
                        fg=DIM if inside else "#4a5160",
                        cursor="fleur" if inside else "arrow", font=("", 9))
        grip.pack(side="left", padx=(4, 2))

        pic = tk.Label(card, image=self.shots.get(pg) or self._blank_shot(),
                       bg="#0a0c0e", bd=0)
        pic.pack(side="left", padx=4, pady=4)

        # The buttons take their room BEFORE the text does. Pack hands out
        # space in the order it is asked for and silently drops whatever no
        # longer fits, which is how the delete button vanished from the one
        # card whose caption ran long.
        btn = tk.Frame(card, bg=CARD)
        btn.pack(side="right", padx=6)

        side = tk.Frame(card, bg=CARD)
        side.pack(side="left", fill="both", expand=True, padx=6)
        tk.Label(side, text=name, bg=CARD, fg="#e6e8ec" if inside else DIM,
                 font=("", 10, "bold")).pack(anchor="w")
        if self.app.slot_of(pg) is not None:
            tk.Label(side, text="yours - double-click", bg=CARD,
                     fg=DIM, font=("", 8)).pack(anchor="w")
        if pg not in self.shots:
            tk.Label(side, text="no picture yet", bg=CARD,
                     fg="#6b7280", font=("", 8)).pack(anchor="w")

        def tool(text, cmd, fg="#e6e8ec"):
            b = tk.Button(btn, text=text, width=2, bd=0, bg="#2b3038", fg=fg,
                          activebackground="#39404a", activeforeground=fg,
                          command=cmd)
            b.pack(side="right", padx=2)
            self._wheel(b)

        # Out of the rotation, or back into it.
        tool("↓" if inside else "↑", lambda p=pg: self.app.pg_toggle(p))
        # A page of your own, made right here rather than at the end: the place
        # you want it is the place you were looking at.
        tool("+", lambda p=pg: self.app.pg_new(after=p))
        if self.app.slot_of(pg) is not None:
            tool("✕", lambda p=pg: self.app.pg_delete(p), fg="#e0806a")

        for wdg in (card, pic, side):
            wdg.bind("<Button-1>", lambda _e, p=pg: self.app.pg_show(p))
            wdg.bind("<Double-Button-1>", lambda _e, p=pg: self.app.pg_open(p))
        for wdg in (card, grip, pic, side, btn):
            self._wheel(wdg)
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

        About three seconds for the lot. Done on demand rather than at startup:
        it moves the panel about, and doing that behind somebody's back the
        moment the app opens would be rude.
        """
        self.app.collect_shots(self._shot_done)

    def _shot_done(self, pg, photo):
        if photo is not None:
            self.shots[pg] = photo
        self.refill()
