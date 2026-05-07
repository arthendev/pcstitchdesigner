"""Dialog for setting the maximum automatic stitch length."""

from PyQt5.QtWidgets import (
    QDialog, QDialogButtonBox, QDoubleSpinBox, QHBoxLayout, QLabel, QVBoxLayout,
)


class AutoStitchLengthDialog(QDialog):
    """Dialog to input a maximum stitch length in mm."""

    def __init__(self, current_value=5.0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Set Maximum Stitch Length")
        self.setFixedSize(300, 100)

        self._spinbox = QDoubleSpinBox()
        self._spinbox.setRange(0.01, 9999.99)
        self._spinbox.setDecimals(2)
        self._spinbox.setSingleStep(0.1)
        self._spinbox.setValue(current_value)

        input_row = QHBoxLayout()
        input_row.addWidget(QLabel("Max. Stitch Length"))
        input_row.addWidget(self._spinbox)
        input_row.addWidget(QLabel("mm"))

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addLayout(input_row)
        layout.addWidget(buttons)
        self.setLayout(layout)

    @property
    def max_length_mm(self):
        """Return the value entered by the user in mm."""
        return self._spinbox.value()
