"""Small animation helpers that respect the reduce-motion setting."""
from PySide6.QtCore import QEasingCurve, QObject, QPropertyAnimation, QVariantAnimation

from ui_qt.theme import theme


def run(obj, prop: bytes, start, end, ms=160, curve=QEasingCurve.Type.OutCubic, finished=None):
    """Animate obj.prop from start to end. With reduce-motion the end value is set at once."""
    if theme.reduce_motion or ms <= 0:
        obj.setProperty(prop.decode(), end)
        if finished:
            finished()
        return None
    a = QPropertyAnimation(obj, prop, obj if isinstance(obj, QObject) else None)
    a.setDuration(ms)
    a.setStartValue(start)
    a.setEndValue(end)
    a.setEasingCurve(curve)
    if finished:
        a.finished.connect(finished)
    a.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
    return a


def value(parent, start, end, ms, step, curve=QEasingCurve.Type.OutCubic, finished=None):
    """Animate a plain number: step(v) is called for every frame. Returns the animation (or None with reduce-motion)."""
    if theme.reduce_motion or ms <= 0:
        step(end)
        if finished:
            finished()
        return None
    a = QVariantAnimation(parent)
    a.setDuration(ms)
    a.setStartValue(float(start))
    a.setEndValue(float(end))
    a.setEasingCurve(curve)
    a.valueChanged.connect(lambda v: step(v))
    if finished:
        a.finished.connect(finished)
    a.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)
    return a
