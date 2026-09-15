"""The notification-area icon, on pywin32.

Written straight onto Shell_NotifyIcon instead of pystray so as not to add a
dependency: pywin32 is already installed. If something goes wrong (another
session, permissions, Windows in an odd mood) the constructor throws and the app
carries on as an ordinary window - a missing icon must never stop it starting.

It runs on its own thread, with its own message loop. Commands reach the
interface through a queue; nothing in Tk is touched from here.
"""

import os
import tempfile
import threading

import win32api
import win32con
import win32gui
from PIL import Image, ImageDraw

WM_TRAY = win32con.WM_USER + 20

MENU_SHOW = 1001
MENU_AUTOSTART = 1002
MENU_QUIT = 1003
MENU_RELEASE = 1004


def _make_icon():
    """The icon, drawn on the spot: a panel with a lit screen."""
    img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 6, 29, 26], radius=3, fill=(32, 34, 40), outline=(120, 130, 145))
    d.rectangle([6, 10, 25, 18], fill=(64, 196, 122))
    d.ellipse([6, 20, 10, 24], fill=(150, 160, 175))
    d.ellipse([13, 20, 17, 24], fill=(150, 160, 175))
    d.ellipse([20, 20, 24, 24], fill=(230, 120, 90))
    path = os.path.join(tempfile.gettempdir(), "picopanel_tray.ico")
    img.save(path, format="ICO", sizes=[(32, 32), (16, 16)])
    return path


class Tray(threading.Thread):
    def __init__(self, queue, title="PicoPanel"):
        super().__init__(daemon=True)
        self.q = queue
        self.title = title
        self.tip = title
        self.autostart = False
        self.hwnd = None
        self._ready = threading.Event()
        self._icon_path = _make_icon()

    # ---------------------------------------------------------------- public
    def set_tip(self, text):
        """The hover text. We use it so the state is visible without opening
        the window."""
        self.tip = text[:127]
        if self.hwnd:
            try:
                self._notify(win32gui.NIM_MODIFY)
            except win32gui.error:
                pass

    def set_autostart(self, on):
        self.autostart = on

    def stop(self):
        if self.hwnd:
            try:
                win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)
            except win32gui.error:
                pass

    # ---------------------------------------------------------------- internal
    def _notify(self, msg):
        hicon = win32gui.LoadImage(0, self._icon_path, win32con.IMAGE_ICON,
                                   0, 0, win32con.LR_LOADFROMFILE)
        win32gui.Shell_NotifyIcon(msg, (self.hwnd, 0,
                                        win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP,
                                        WM_TRAY, hicon, self.tip))

    def _on_message(self, hwnd, msg, wparam, lparam):
        if lparam in (win32con.WM_LBUTTONDBLCLK, win32con.WM_LBUTTONUP):
            self.q.put(("show", None))
        elif lparam == win32con.WM_RBUTTONUP:
            self._menu()
        return 0

    def _menu(self):
        m = win32gui.CreatePopupMenu()
        win32gui.AppendMenu(m, win32con.MF_STRING, MENU_SHOW, "Show the panel")
        win32gui.AppendMenu(m, win32con.MF_STRING, MENU_RELEASE,
                            "Release the port for 60s (for flashing)")
        win32gui.AppendMenu(m, win32con.MF_SEPARATOR, 0, "")
        flags = win32con.MF_STRING | (win32con.MF_CHECKED if self.autostart else 0)
        win32gui.AppendMenu(m, flags, MENU_AUTOSTART, "Start at logon")
        win32gui.AppendMenu(m, win32con.MF_SEPARATOR, 0, "")
        win32gui.AppendMenu(m, win32con.MF_STRING, MENU_QUIT, "Quit")
        x, y = win32gui.GetCursorPos()
        # Without SetForegroundWindow the menu stays open after you click
        # elsewhere - that's TrackPopupMenu's documented behaviour.
        win32gui.SetForegroundWindow(self.hwnd)
        win32gui.TrackPopupMenu(m, win32con.TPM_LEFTALIGN, x, y, 0, self.hwnd, None)
        win32gui.PostMessage(self.hwnd, win32con.WM_NULL, 0, 0)

    def _on_command(self, hwnd, msg, wparam, lparam):
        cmd = win32api.LOWORD(wparam)
        if cmd == MENU_SHOW:
            self.q.put(("show", None))
        elif cmd == MENU_AUTOSTART:
            self.q.put(("autostart", not self.autostart))
        elif cmd == MENU_RELEASE:
            self.q.put(("release", None))
        elif cmd == MENU_QUIT:
            self.q.put(("quit", None))
        return 0

    def _on_close_msg(self, hwnd, msg, wparam, lparam):
        # DestroyWindow returns None and the WNDPROC needs an integer - which is
        # why it can't go straight into the message table as a lambda.
        win32gui.DestroyWindow(hwnd)
        return 0

    def _on_destroy(self, hwnd, msg, wparam, lparam):
        try:
            win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, (self.hwnd, 0))
        except win32gui.error:
            pass
        win32gui.PostQuitMessage(0)
        return 0

    def run(self):
        wc = win32gui.WNDCLASS()
        wc.hInstance = win32api.GetModuleHandle(None)
        wc.lpszClassName = "PicoPanelTray"
        wc.lpfnWndProc = {
            WM_TRAY: self._on_message,
            win32con.WM_COMMAND: self._on_command,
            win32con.WM_DESTROY: self._on_destroy,
            win32con.WM_CLOSE: self._on_close_msg,
        }
        try:
            win32gui.RegisterClass(wc)
        except win32gui.error:
            pass            # already registered by an earlier run
        self.hwnd = win32gui.CreateWindow(
            "PicoPanelTray", self.title, win32con.WS_OVERLAPPED,
            0, 0, 0, 0, 0, 0, wc.hInstance, None)
        win32gui.UpdateWindow(self.hwnd)
        self._notify(win32gui.NIM_ADD)
        self._ready.set()
        win32gui.PumpMessages()
