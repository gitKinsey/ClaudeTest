"""Inspector tabs of the Keys page that build actions: key combo + text + computer actions, the macro sequence, gestures and alternate / random keys."""
from PySide6.QtWidgets import QWidget

from ui_qt.layouts import vbox


def build_builder(page):
    w = QWidget()
    vbox(w)
    return w


def build_sequence(page):
    w = QWidget()
    vbox(w)
    return w


def build_gestures(page):
    w = QWidget()
    vbox(w)
    return w
