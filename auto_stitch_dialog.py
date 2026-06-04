"""Dialog for setting the maximum automatic stitch length."""

from PyQt5.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QHBoxLayout, QLabel, QVBoxLayout,
)


class AutoStitchLengthDialog(QDialog):
    """Dialog to input a maximum stitch length in mm."""

    def __init__(self, current_value=5.0, max_dx_active=True, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Set Maximum Stitch Length"))
        self.setFixedSize(300, 100)

        self._spinbox = QDoubleSpinBox()
        self._spinbox.setRange(0.01, 9999.99)
        self._spinbox.setDecimals(2)
        self._spinbox.setSingleStep(0.1)
        self._spinbox.setValue(current_value)
        self._spinbox.setToolTip(
            self.tr("Limit the maximum distance between consecutive stitches.")
            + "\n" +
            self.tr("Auto-stitches are inserted in between when this is exceeded.")
        )

        input_row = QHBoxLayout()
        input_row.addWidget(QLabel(self.tr("Max. Stitch Length")))
        input_row.addWidget(self._spinbox)
        input_row.addWidget(QLabel(self.tr("mm")))

        self._max_dx_checkbox = QCheckBox(self.tr("Max. dx: 6 mm"))
        self._max_dx_checkbox.setChecked(max_dx_active)
        self._max_dx_checkbox.setToolTip(
            self.tr("Limit the longitudinal distance (fabric transport) between consecutive stitches to 6 mm.")
            + "\n" +
            self.tr("Auto-stitches are inserted in between when this is exceeded.")
        )

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addLayout(input_row)
        layout.addWidget(self._max_dx_checkbox)
        layout.addWidget(buttons)
        self.setLayout(layout)

    @property
    def max_length_mm(self):
        """Return the value entered by the user in mm."""
        return self._spinbox.value()

    @property
    def max_dx_active(self):
        """Return whether the max-dx constraint is enabled."""
        return self._max_dx_checkbox.isChecked()
