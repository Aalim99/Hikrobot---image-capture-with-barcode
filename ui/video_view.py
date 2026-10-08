"""The live view, with the scan-area box drawn on top and editable in place.

Normal mode shows the committed scan area (the rest of the picture is dimmed
so it is obvious where the camera is looking for barcodes) plus any barcode
that was read.

Edit mode lets the operator drag the box: drag on empty space to draw a new
one, drag inside it to move, drag a handle to resize. All the geometry lives
in roi.py in normalised image coordinates; this widget only maps the mouse
between screen space and that.
"""
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

import roi as roi_mod
from imaging import fit_letterbox
from ui.theme import C, mono, qcolor, sans

HANDLE_PX = 11          # drawn size of a resize handle
GRAB_PX = 12            # how close the mouse must be to grab an edge/corner
MIN_DRAG_PX = 4         # a click that barely moves does not start a new box

CURSORS = {
    "nw": Qt.SizeFDiagCursor, "se": Qt.SizeFDiagCursor,
    "ne": Qt.SizeBDiagCursor, "sw": Qt.SizeBDiagCursor,
    "n": Qt.SizeVerCursor, "s": Qt.SizeVerCursor,
    "e": Qt.SizeHorCursor, "w": Qt.SizeHorCursor,
    "move": Qt.SizeAllCursor, "draw": Qt.CrossCursor,
}


class VideoView(QWidget):
    editChanged = Signal()        # the working box changed (a drag finished)
    editFinished = Signal(bool)   # Enter (True = keep) or Esc (False = discard)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(520, 340)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

        self._image = None
        self._buffer = None            # keeps the numpy memory behind _image alive
        self._frame_size = (0, 0)
        self._detections = []
        self._unreadable = None
        self._offline = None           # (title, detail) when there is no picture

        self._roi = None               # committed scan area
        self._editing = False
        self._work = None              # box being edited
        self._drag = None              # (mode, start_box, start_point)
        self._drag_moved = False

    # ---------- feeding the view ----------

    def show_frame(self, rgb, frame_width, frame_height):
        """rgb: contiguous (h, w, 3) uint8 preview of a frame_width x frame_height frame."""
        h, w = rgb.shape[:2]
        self._buffer = rgb
        self._image = QImage(rgb.data, w, h, rgb.strides[0], QImage.Format_RGB888)
        self._frame_size = (frame_width, frame_height)
        self._offline = None
        self.update()

    def show_offline(self, title, detail):
        self._image = None
        self._buffer = None
        self._offline = (title, detail)
        self.update()

    def set_overlays(self, detections, unreadable_rect):
        self._detections = list(detections)
        self._unreadable = unreadable_rect
        self.update()

    def set_roi(self, box):
        self._roi = box
        self.update()

    # ---------- editing ----------

    @property
    def editing(self) -> bool:
        return self._editing

    @property
    def working_roi(self):
        return self._work

    def begin_edit(self):
        self._editing = True
        self._work = self._roi
        self._drag = None
        self.setFocus()
        self.update()

    def end_edit(self, commit: bool):
        """Leave edit mode; returns the scan area now in force."""
        if commit:
            self._roi = self._work
        self._editing = False
        self._work = None
        self._drag = None
        self.unsetCursor()
        self.update()
        return self._roi

    def clear_working_roi(self):
        self._work = None
        self.update()
        self.editChanged.emit()

    # ---------- geometry ----------

    def _image_rect(self) -> QRectF:
        fw, fh = self._frame_size
        if fw <= 0 or fh <= 0:
            return QRectF()
        dw, dh, ox, oy, _scale = fit_letterbox(fw, fh, self.width(), self.height())
        return QRectF(ox, oy, dw, dh)

    def _to_norm(self, pos):
        rect = self._image_rect()
        if rect.isEmpty():
            return None
        x = min(max((pos.x() - rect.left()) / rect.width(), 0.0), 1.0)
        y = min(max((pos.y() - rect.top()) / rect.height(), 0.0), 1.0)
        return x, y

    def _roi_to_screen(self, box, rect=None) -> QRectF:
        rect = rect or self._image_rect()
        return QRectF(rect.left() + box.x * rect.width(), rect.top() + box.y * rect.height(),
                      box.w * rect.width(), box.h * rect.height())

    def _frame_to_screen(self, frame_rect, rect) -> QRectF:
        fw, fh = self._frame_size
        left, top, w, h = frame_rect
        return QRectF(rect.left() + left / fw * rect.width(), rect.top() + top / fh * rect.height(),
                      w / fw * rect.width(), h / fh * rect.height())

    def _tolerance(self):
        rect = self._image_rect()
        return GRAB_PX / max(rect.width(), 1.0), GRAB_PX / max(rect.height(), 1.0)

    # ---------- mouse / keyboard ----------

    def mousePressEvent(self, event):
        if not self._editing or event.button() != Qt.LeftButton:
            return
        point = self._to_norm(event.position())
        if point is None:
            return
        self.setFocus()
        hit = roi_mod.hit_test(self._work, point, *self._tolerance()) if self._work else None
        self._drag = (hit or "draw", self._work, point)
        self._drag_moved = False

    def mouseMoveEvent(self, event):
        if not self._editing:
            return
        point = self._to_norm(event.position())
        if point is None:
            return
        if self._drag is None:
            hit = roi_mod.hit_test(self._work, point, *self._tolerance()) if self._work else None
            self.setCursor(CURSORS[hit or "draw"])
            return

        mode, start_box, start_point = self._drag
        rect = self._image_rect()
        travelled = max(abs(point[0] - start_point[0]) * rect.width(),
                        abs(point[1] - start_point[1]) * rect.height())
        if not self._drag_moved and travelled < MIN_DRAG_PX:
            return
        self._drag_moved = True
        if mode == "draw":
            self._work = roi_mod.from_points(start_point, point)
        else:
            self._work = roi_mod.drag(mode, start_box, start_point, point)
        self.update()

    def mouseReleaseEvent(self, event):
        if self._drag is None:
            return
        moved = self._drag_moved
        self._drag = None
        if moved:
            self._work = roi_mod.clamp(self._work)
            self.update()
            self.editChanged.emit()

    def keyPressEvent(self, event):
        if self._editing and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.editFinished.emit(True)
        elif self._editing and event.key() == Qt.Key_Escape:
            self.editFinished.emit(False)
        else:
            super().keyPressEvent(event)

    # ---------- painting ----------

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing
                               | QPainter.SmoothPixmapTransform)
        outer = QPainterPath()
        outer.addRoundedRect(QRectF(self.rect()), 10, 10)
        painter.setClipPath(outer)
        painter.fillPath(outer, qcolor("video_bg"))

        if self._image is None:
            self._paint_placeholder(painter)
            return

        rect = self._image_rect()
        painter.drawImage(rect, self._image)

        box = self._work if self._editing else self._roi
        if box is not None:
            self._paint_scan_area(painter, rect, box)
        self._paint_detections(painter, rect)
        self._paint_banner(painter, rect)

    def _paint_placeholder(self, painter):
        title, detail = self._offline or ("STARTING…", "waiting for the first frame")
        tone = "err" if self._offline else "muted"
        painter.setPen(qcolor(tone))
        painter.setFont(sans(16, 700))
        area = QRectF(self.rect())
        painter.drawText(area.adjusted(0, -24, 0, -24), Qt.AlignCenter, title)
        painter.setPen(qcolor("dim"))
        painter.setFont(sans(10))
        painter.drawText(area.adjusted(40, 26, -40, 26), Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, detail)

    def _paint_scan_area(self, painter, rect, box):
        screen = self._roi_to_screen(box, rect)

        # dim everything outside the box so the scan area stands out
        shade = QPainterPath()
        shade.addRect(rect)
        hole = QPainterPath()
        hole.addRect(screen)
        painter.fillPath(shade.subtracted(hole), qcolor("video_bg", 150 if self._editing else 105))

        colour = "warn" if self._editing else "accent"
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(qcolor(colour), 2 if self._editing else 1.5))
        painter.drawRect(screen)
        if not self._editing:
            self._paint_corners(painter, screen, colour)

        label = "SCAN AREA · EDITING" if self._editing else "SCAN AREA"
        self._chip(painter, label, screen.left(), screen.top() - 6, colour, "#06121f" if not self._editing else "#2a1c00",
                   above=True, bounds=rect)

        if self._editing:
            self._paint_handles(painter, screen)

    def _paint_corners(self, painter, screen, colour):
        length = min(22.0, screen.width() / 3, screen.height() / 3)
        painter.setPen(QPen(qcolor(colour), 4, Qt.SolidLine, Qt.FlatCap))
        for cx, cy, dx, dy in ((screen.left(), screen.top(), 1, 1), (screen.right(), screen.top(), -1, 1),
                               (screen.left(), screen.bottom(), 1, -1), (screen.right(), screen.bottom(), -1, -1)):
            painter.drawLine(QPointF(cx, cy), QPointF(cx + dx * length, cy))
            painter.drawLine(QPointF(cx, cy), QPointF(cx, cy + dy * length))

    def _paint_handles(self, painter, screen):
        xs = (screen.left(), screen.center().x(), screen.right())
        ys = (screen.top(), screen.center().y(), screen.bottom())
        painter.setPen(QPen(qcolor("warn"), 2))
        painter.setBrush(qcolor("text"))
        for ix, x in enumerate(xs):
            for iy, y in enumerate(ys):
                if ix == 1 and iy == 1:
                    continue
                painter.drawRoundedRect(QRectF(x - HANDLE_PX / 2, y - HANDLE_PX / 2, HANDLE_PX, HANDLE_PX), 3, 3)

    def _paint_detections(self, painter, rect):
        fw, fh = self._frame_size
        if fw <= 0 or fh <= 0:
            return
        for det in self._detections:
            screen = self._frame_to_screen(det.rect, rect)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(qcolor("ok"), 3))
            painter.drawRect(screen)
            self._chip(painter, det.text, screen.left(), screen.top() - 6, "ok", "#04210f",
                       above=True, bounds=rect, font=mono(11, 700))

        if self._unreadable is not None and not self._detections:
            screen = self._frame_to_screen(self._unreadable, rect)
            pen = QPen(qcolor("warn"), 3, Qt.DashLine)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(pen)
            painter.drawRect(screen)
            self._chip(painter, "barcode too small or blurred", screen.left(), screen.bottom() + 6,
                       "warn", "#2a1c00", above=False, bounds=rect)

    def _paint_banner(self, painter, rect):
        if self._editing:
            self._chip(painter, "Drag to draw the scan area  ·  drag the box to move  ·  drag a handle to resize"
                       "      Enter = done    Esc = cancel",
                       rect.center().x(), rect.top() + 14, "panel_hi", C["text"], center=True, bounds=rect,
                       border=True)
        elif self._roi is None:
            self._chip(painter, "No scan area set  -  press  ✎ Edit area  and drag a box around the barcode",
                       rect.center().x(), rect.bottom() - 14, "panel_hi", C["text"], center=True,
                       above=True, bounds=rect, border=True)

    def _chip(self, painter, text, x, y, fill, ink, above=False, center=False,
              bounds=None, font=None, border=False):
        """A small rounded label anchored at (x, y); kept inside `bounds`.

        `fill` is a palette key, `ink` the text colour as a hex string."""
        font = font or sans(9, 700)
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        w = metrics.horizontalAdvance(text) + 18
        h = metrics.height() + 8
        left = x - w / 2 if center else x
        top = y - h if above else y
        if bounds is not None:
            left = min(max(left, bounds.left() + 4), max(bounds.right() - w - 4, bounds.left() + 4))
            top = min(max(top, bounds.top() + 4), max(bounds.bottom() - h - 4, bounds.top() + 4))
        body = QRectF(left, top, w, h)
        painter.setPen(QPen(qcolor("border"), 1) if border else Qt.NoPen)
        painter.setBrush(qcolor(fill))
        painter.drawRoundedRect(body, 6, 6)
        painter.setPen(QColor(ink))
        painter.drawText(body, Qt.AlignCenter, text)
