"""PyQt6 system-tray UI: connection state, pairing mode toggle, PIN entry.

Optional — the brain runs headless (`milo-brain --headless`) without PyQt6.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable


def run_tray(
    server,
    *,
    on_quit: Callable[[], None],
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Blocking Qt main loop; the asyncio server runs in a background thread."""
    from PyQt6.QtGui import QAction, QIcon, QPixmap, QColor
    from PyQt6.QtWidgets import (
        QApplication,
        QInputDialog,
        QLineEdit,
        QMenu,
        QSystemTrayIcon,
    )

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)

    def make_icon(color: str) -> QIcon:
        pixmap = QPixmap(24, 24)
        pixmap.fill(QColor(color))
        return QIcon(pixmap)

    tray = QSystemTrayIcon(make_icon("#888888"))
    tray.setToolTip("Milo Brain — no robot connected")
    menu = QMenu()

    pairing_action = QAction("Enable pairing mode")
    pairing_action.setCheckable(True)

    def toggle_pairing(checked: bool) -> None:
        server.advertiser.update(pairing=checked)

    pairing_action.toggled.connect(toggle_pairing)
    menu.addAction(pairing_action)

    quit_action = QAction("Quit")
    quit_action.triggered.connect(lambda: (on_quit(), app.quit()))
    menu.addAction(quit_action)
    tray.setContextMenu(menu)
    tray.show()

    def refresh() -> None:
        robot = server.connected_robot
        if robot is not None:
            tray.setIcon(make_icon("#2ecc71"))
            tray.setToolTip(f"Milo Brain — connected: {robot.name}")
        else:
            tray.setIcon(make_icon("#888888"))
            tray.setToolTip("Milo Brain — no robot connected")

    from PyQt6.QtCore import QTimer

    timer = QTimer()
    timer.timeout.connect(refresh)
    timer.start(1000)

    def request_pin_blocking(robot_name: str) -> str | None:
        pin, ok = QInputDialog.getText(
            None,
            "Pair with Milo",
            f"Robot '{robot_name}' wants to pair.\nEnter the 6-digit PIN on its face:",
            QLineEdit.EchoMode.Normal,
        )
        return pin.strip() if ok and pin.strip() else None

    # The server asks for PINs from its asyncio thread; marshal to the Qt thread.
    async def request_pin(robot_name: str) -> str | None:
        result: asyncio.Future = loop.create_future()

        def ask() -> None:
            value = request_pin_blocking(robot_name)
            loop.call_soon_threadsafe(result.set_result, value)

        QTimer.singleShot(0, ask)
        return await result

    server._request_pin = request_pin  # inject the interactive PIN dialog
    app.exec()
