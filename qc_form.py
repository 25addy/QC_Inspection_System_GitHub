import sys
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QDialog, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QLineEdit, QTextEdit, QPushButton, QComboBox,
    QCheckBox, QRadioButton, QGroupBox, QScrollArea, QFrame, QMessageBox,
    QSpinBox, QDoubleSpinBox, QToolButton, QSizePolicy, QStackedWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QGraphicsOpacityEffect,
    QButtonGroup          # ← ADD THIS
)

from PySide6.QtWidgets import QProgressBar, QWidget, QHBoxLayout
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QPainter, QPen, QPageSize, QPixmap, QColor
from PySide6.QtPrintSupport import QPrinter
from datetime import datetime
import os

import mysql.connector
import os
import hashlib



# ====================== ADMIN SIGNUP ACCESS ======================
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "change_me")

# ====================== MYSQL CONFIG ======================
DB_ENABLED = True
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "gama")
DB_TABLE = os.getenv("DB_TABLE", "gama_loop_dt")
DB_TABLE_PROD = os.getenv("DB_TABLE_PROD", "gama_production_planning")   # for production_no → loop_size, pcs_bag
# New QC header + bag tables
DB_QC_HEADER = os.getenv("DB_QC_HEADER", "gama_qc_carton_header")
DB_QC_BAG = os.getenv("DB_QC_BAG", "gama_qc_carton_bag")
DB_LOOP_DT_BAG = os.getenv("DB_LOOP_DT_BAG", "gama_loop_dt_bag")
USERS_TABLE = os.getenv("USERS_TABLE", "qc_users")


def hash_password(password: str) -> tuple[bytes, bytes]:
    """
    Returns (salt, hash).
    PBKDF2-SHA256: strong + built-in (no extra pip install).
    """
    salt = os.urandom(16)
    pwd_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        120_000,   # iterations
        dklen=32
    )
    return salt, pwd_hash

def verify_password(password: str, salt: bytes, stored_hash: bytes) -> bool:
    test_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        120_000,
        dklen=32
    )
    return test_hash == stored_hash


# ====================== MODBUS SCALE READER ======================

from pymodbus.client import ModbusSerialClient

def read_scale_weight(port='COM3'):
    """
    Read weight from Modbus weighing scale (same logic as Test6.py)
    and return weight in kg (float) or None on error.
    """
    try:
        client = ModbusSerialClient(
            port=port,
            baudrate=9600,
            stopbits=1,
            bytesize=8,
            parity='N',
            timeout=1,      # same as Test6.py
        )

        if not client.connect():
            print("Failed to connect to scale")
            return None

        # 🔹 SAME AS Test6.py: read 2 registers starting at 82, use slave=1
        resp = client.read_holding_registers(82, 2, slave=1)

        if hasattr(resp, "isError") and resp.isError():
            print("Modbus read error:", resp)
            return None

        # 🔹 SAME decode as Test6: 32-bit signed, *0.1 g
        high, low = resp.registers
        combined = (high << 16) | low
        if combined >= (1 << 31):
            combined -= (1 << 32)

        weight_g = int(combined * 0.1)     # grams
        weight_kg = weight_g / 1000.0      # to kg

        client.close()
        return weight_kg

    except Exception as e:
        print("Scale error:", e)
        return None



# ---------------- QC Checking Form Window ---------------- #

class QCFormWindow(QWidget):

    def _create_labeled_edit(self, grid, row, col,
                             label_text, placeholder="",
                             required=False, read_only=False):
        """
        Helper to create: LABEL (top) + QLineEdit (bottom) in a grid cell.
        Returns the QLineEdit so we can store it in self.xxx_edit.
        """
        wrapper = QWidget()
        v = QVBoxLayout(wrapper)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        label = QLabel(f"{label_text}{' *' if required else ''}")
        label.setObjectName("FieldLabel")      # uses existing style
        v.addWidget(label)

        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        if read_only:
            edit.setEnabled(False)
        v.addWidget(edit)

        grid.addWidget(wrapper, row, col)
        return edit


    def __init__(self, parent=None):
        super().__init__(parent)

        # ✅ Track selected Bag Number across bags (avoid duplicates in Digital Weight Scale)
        self._scale_used_bag_numbers = set()
        self._scale_bag_selected_by_index = {}  # {bag_index:int -> "n/total"}
        self._scale_last_carton_for_bags = None

        self._apply_styles()

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 24, 24, 24)
        main_layout.setSpacing(16)


        # Scroll area for content
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)  # <--- ADD THIS
        scroll_content = QWidget()
        
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setSpacing(30)
        self.scroll_area.setWidget(scroll_content)
        main_layout.addWidget(self.scroll_area)
        # ===== OUTSIDE CARD : Page Header =====
        page_header = QWidget()
        page_header.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        page_header.setMaximumHeight(120)   # you can adjust: 90 / 100 / 120

        header_layout = QVBoxLayout(page_header)
        header_layout.setContentsMargins(0, 0, 0, 8)
        header_layout.setSpacing(4)

        page_title = QLabel("Start New Inspection")
        page_title.setStyleSheet(
            "font-size: 26pt; font-weight: 700; color: #111827;"
        )

        page_subtitle = QLabel("Begin QC for a new carton")
        page_subtitle.setStyleSheet(
            "font-size: 14pt; color: #6B7280;"
        )

        header_layout.addWidget(page_title)
        header_layout.addWidget(page_subtitle)

        scroll_layout.addWidget(page_header)

          # Carton Box Information
        carton_info = QFrame()
        carton_info.setObjectName("SectionFrame")
        info_layout = QVBoxLayout(carton_info)
        info_layout.setSpacing(15)

        info_title = QLabel("<span style='font-size:32px;'>📦</span>  Carton Box Information")
        info_title.setObjectName("SectionTitle")
        info_layout.addWidget(info_title)

        fields_layout = QGridLayout()
        fields_layout.setSpacing(20)

        # Top row (4 columns)
        self.carton_no_edit = self._create_labeled_edit(
            fields_layout, 0, 0,
            "Carton Number", "",
            required=True
        )
        self.checked_by_edit = self._create_labeled_edit(
            fields_layout, 0, 1,
            "Checked By", "",
            required=True
        )

        self.production_no_edit = self._create_labeled_edit(
            fields_layout, 0, 2,
            "Production Number", "",
            required=True,
            read_only=True
        )

        self.operator_name_edit = self._create_labeled_edit(
            fields_layout, 0, 3,
            "Operator Packing Name", "NAME",
            required=True,
            read_only=True
        )


        # Second row (3 columns)
        self.loop_size_view_edit = self._create_labeled_edit(
            fields_layout, 1, 0,
            "Loop Size", "",
            required=True,
            read_only=True   # view-only from DB
        )

        self.pcs_per_bag_edit = self._create_labeled_edit(
            fields_layout, 1, 1,
            "PCS per Bag", "",
            required=True,
            read_only=True   # view-only from DB
        )

        self.total_bags_edit = self._create_labeled_edit(
            fields_layout, 1, 2,
            "Total Bags in Carton", "",
            required=True,
            read_only=True
        )

        # Row 1, column 4 → QC Number
        self.qc_no_edit = self._create_labeled_edit(
            fields_layout, 1, 3,
            "QC Number", "",
            required=True
        )

        # Third row – Weight Limits
        self.min_weight_edit = self._create_labeled_edit(
            fields_layout, 2, 0,
            "Minimum Weight", "",
            required=True,
            read_only=True
        )

        self.avg_weight_edit = self._create_labeled_edit(
            fields_layout, 2, 1,
            "Average Weight", "",
            required=True,
            read_only=True
        )

        self.max_weight_edit = self._create_labeled_edit(
            fields_layout, 2, 2,
            "Maximum Weight", "",
            required=True,
            read_only=True
        )
        # --- Unloading Operator + Bag Number (same row) ---
        uo_wrapper = QWidget()
        uo_layout = QHBoxLayout(uo_wrapper)
        uo_layout.setContentsMargins(0, 0, 0, 0)
        uo_layout.setSpacing(10)

        # Unloading Operator (left)
        uo_box = QWidget()
        uo_v = QVBoxLayout(uo_box)
        uo_v.setContentsMargins(0, 0, 0, 0)
        uo_v.setSpacing(4)

        uo_label = QLabel("Unloading Operator *")
        uo_label.setObjectName("FieldLabel")
        uo_v.addWidget(uo_label)

        self.unloading_operator_edit = QLineEdit()
        uo_v.addWidget(self.unloading_operator_edit)

        uo_layout.addWidget(uo_box, 2)  # wider

        # Bag Number (right)
        bag_box = QWidget()
        bag_v = QVBoxLayout(bag_box)
        bag_v.setContentsMargins(0, 0, 0, 0)
        bag_v.setSpacing(4)

        bag_label = QLabel("Bag Number")
        bag_label.setObjectName("FieldLabel")
        bag_v.addWidget(bag_label)

        self.bag_number_combo = QComboBox()
        self.bag_number_combo.addItem("Select Bag")
        bag_v.addWidget(self.bag_number_combo)

        uo_layout.addWidget(bag_box, 1)  # smaller

        # Add to grid
        fields_layout.addWidget(uo_wrapper, 3, 0, 1, 2)



        info_layout.addLayout(fields_layout)

        start_btn = QPushButton("Start Inspection")
        start_btn.setObjectName("StartButton")
        start_btn.clicked.connect(self.start_inspection)
        info_layout.addWidget(start_btn)

        start_btn.setObjectName("StartButton")
        start_btn.clicked.connect(self.start_inspection)
        info_layout.addWidget(start_btn)

        scroll_layout.addWidget(carton_info)

        # Carton Box Condition
        condition_frame = QFrame()
        condition_frame.setObjectName("SectionFrame")
        condition_layout = QVBoxLayout(condition_frame)
        condition_layout.setSpacing(18)

        # Section title
        condition_title = QLabel("<span style='font-size:32px;'>📦</span>  Carton Box Condition")
        condition_title.setObjectName("SectionTitle")
        condition_layout.addWidget(condition_title)

        # Main 2-column layout (left = cards, right = remarks)
        body_layout = QGridLayout()
        body_layout.setSpacing(16)
        body_layout.setColumnStretch(0, 1)
        body_layout.setColumnStretch(1, 1)

        # Row 0 → labels
        box_label = QLabel("Box Condition *")
        box_label.setObjectName("FieldLabel")
        body_layout.addWidget(box_label, 0, 0)

        remarks_label = QLabel("Box Remarks")
        remarks_label.setObjectName("FieldLabel")
        body_layout.addWidget(remarks_label, 0, 1)

        # ---------- LEFT SIDE : CARD-STYLE OPTIONS ----------
        cards_col = QVBoxLayout()
        cards_col.setSpacing(12)

                # OK card
        self.box_ok = QRadioButton()
        self.box_ok.setObjectName("BoxOkRadio")

        self.ok_card = QFrame()
        self.ok_card.setObjectName("BoxCard")
        ok_layout = QVBoxLayout(self.ok_card)
        ok_layout.setContentsMargins(16, 12, 16, 12)
        ok_layout.setSpacing(4)

        # top row: radio + title
        ok_top = QHBoxLayout()
        ok_top.setSpacing(10)
        ok_top.addWidget(self.box_ok, 0, Qt.AlignTop)

        ok_title = QLabel("OK - Box in Good Condition")
        ok_title.setObjectName("BoxOkTitle")
        ok_top.addWidget(ok_title)
        ok_top.addStretch()
        ok_layout.addLayout(ok_top)

        # Subtitle wrapper WITHOUT border
        ok_sub_frame = QWidget()
        ok_sub_frame.setObjectName("SubtitleFrame")
        ok_sub_layout = QVBoxLayout(ok_sub_frame)
        ok_sub_layout.setContentsMargins(0, 8, 0, 0)
        ok_sub_layout.setSpacing(0)

        ok_sub = QLabel("No damage, proper sealing, clean")
        ok_sub.setObjectName("BoxSubtitle")
        ok_sub_layout.addWidget(ok_sub)

        ok_layout.addWidget(ok_sub_frame)

        cards_col.addWidget(self.ok_card)


        # NO card
        self.box_no = QRadioButton()
        self.box_no.setObjectName("BoxNoRadio")

        self.no_card = QFrame()
        self.no_card.setObjectName("BoxCard")
        no_layout = QVBoxLayout(self.no_card)
        no_layout.setContentsMargins(16, 12, 16, 12)
        no_layout.setSpacing(4)

        no_top = QHBoxLayout()
        no_top.setSpacing(10)
        no_top.addWidget(self.box_no, 0, Qt.AlignTop)

        no_title = QLabel("No - Box Damaged")
        no_title.setObjectName("BoxNoTitle")
        no_top.addWidget(no_title)
        no_top.addStretch()
        no_layout.addLayout(no_top)

        no_sub_frame = QWidget()
        no_sub_frame.setObjectName("SubtitleFrame")
        no_sub_layout = QVBoxLayout(no_sub_frame)
        no_sub_layout.setContentsMargins(0, 8, 0, 0)
        no_sub_layout.setSpacing(0)

        no_sub = QLabel("Torn, wet, crushed, or improperly sealed")
        no_sub.setObjectName("BoxSubtitle")
        no_sub_layout.addWidget(no_sub)

        no_layout.addWidget(no_sub_frame)


        cards_col.addWidget(self.no_card)

        # Group the two radios so only one can be chosen
        self.box_group = QButtonGroup(self)
        self.box_group.addButton(self.box_ok)
        self.box_group.addButton(self.box_no)
        self.box_group.setExclusive(True)

        body_layout.addLayout(cards_col, 1, 0)

        # ---------- RIGHT SIDE : REMARKS ----------
        self.box_remarks = QTextEdit()
        self.box_remarks.setPlaceholderText(
            "Any specific remarks about the carton box condition..."
        )
        self.box_remarks.setMinimumHeight(150)
        body_layout.addWidget(self.box_remarks, 1, 1)

        condition_layout.addLayout(body_layout)

        # Proceed button (bottom)
        proceed_btn = QPushButton("➜  Proceed to Bag Inspection")
        proceed_btn.setObjectName("ProceedButton")
        proceed_btn.setMinimumHeight(44)
        proceed_btn.clicked.connect(self.proceed_to_bags)
        condition_layout.addWidget(proceed_btn)

        self.condition_frame = condition_frame      # store reference
        self.condition_frame.hide()                 # hide at start
        scroll_layout.addWidget(self.condition_frame)

        # Make cards clickable + apply initial style
        self.box_ok.toggled.connect(self._update_box_condition_styles)
        self.box_no.toggled.connect(self._update_box_condition_styles)

        def make_card_click_handler(radio):
            def handler(event):
                radio.setChecked(True)
                self._update_box_condition_styles()
            return handler

        self.ok_card.mousePressEvent = make_card_click_handler(self.box_ok)
        self.no_card.mousePressEvent = make_card_click_handler(self.box_no)

        self._update_box_condition_styles()
       

        # ===== Bag Inspection SECTION (only progress + bag cards) =====
        self.bag_section = QFrame()
        self.bag_section.setObjectName("SectionFrame")
        bag_section_layout = QVBoxLayout(self.bag_section)
        bag_section_layout.setSpacing(20)

        # Inspection Progress
        progress_layout = QHBoxLayout()
        progress_title = QLabel("Inspection Progress")
        progress_title.setObjectName("ProgressTitle")
        progress_layout.addWidget(progress_title)
        progress_layout.addStretch()
        self.progress_status = QLabel("0 / 15 Bags")
        progress_layout.addWidget(self.progress_status)
        bag_section_layout.addLayout(progress_layout)

        # Bag Inspection grid
        self.bag_grid = QGridLayout()
        self.bag_cards = []
        self.total_bags = 15  # Default
        self._create_bag_cards()
        self._reset_bag_visibility()           # only Bag #1 visible
        bag_section_layout.addLayout(self.bag_grid)

        # this SectionFrame is **only** for bags
        self.bag_section.hide()
        scroll_layout.addWidget(self.bag_section)

        # ---- QC Final Result section (own purple card) ----
        self.overall_frame = QFrame()
        self.overall_frame.setObjectName("OverallResultFrame")
        overall_frame_layout = QHBoxLayout(self.overall_frame)
        overall_frame_layout.setContentsMargins(16, 8, 16, 8)
        overall_frame_layout.setSpacing(12)


        overall_label = QLabel("<span style='font-size:34px;'>✔️</span>  QC Final Result:")
        overall_label.setObjectName("OverallLabel")

        overall_toggle = QHBoxLayout()
        overall_toggle.setSpacing(12)

        self.overall_pass = QPushButton("PASS")
        self.overall_fail = QPushButton("FAIL")
        self.overall_pass.setCheckable(True)
        self.overall_fail.setCheckable(True)

        self.overall_pass.setChecked(False)
        self.overall_fail.setChecked(False)

        self.overall_pass.setObjectName("OverallPassButton")
        self.overall_fail.setObjectName("OverallFailButton")

        self.overall_pass.clicked.connect(lambda: self.set_overall_status(True))
        self.overall_fail.clicked.connect(lambda: self.set_overall_status(False))

        overall_toggle.addWidget(self.overall_pass)
        overall_toggle.addWidget(self.overall_fail)

        overall_frame_layout.addWidget(overall_label)
        overall_frame_layout.addSpacing(8)
        overall_frame_layout.addLayout(overall_toggle)
        overall_frame_layout.addStretch()



        # ----- Inspection Summary section (own white card) -----
        self.summary_frame = QFrame()
        self.summary_frame.setObjectName("SummaryFrame")
        summary_outer = QVBoxLayout(self.summary_frame)
        summary_outer.setContentsMargins(16, 16, 16, 16)
        summary_outer.setSpacing(12)

        summary_header = QHBoxLayout()
        summary_icon = QLabel("<span style='font-size:34px;'>📊</span>")
        summary_icon.setObjectName("SummaryIcon")
        summary_title_lbl = QLabel("Inspection Summary")
        summary_title_lbl.setObjectName("SummarySectionTitle")
        summary_title_lbl.setStyleSheet("font-size: 22px; font-weight: 700;")
        summary_header.addWidget(summary_icon)
        summary_header.addSpacing(4)
        summary_header.addWidget(summary_title_lbl)
        summary_header.addStretch()
        summary_outer.addLayout(summary_header)

        summary_layout = QHBoxLayout()
        summary_layout.setSpacing(12)

        def make_summary_card(attr_name, title, obj_name):
            card = QFrame()
            card.setObjectName(obj_name)
            cl = QVBoxLayout(card)
            cl.setContentsMargins(24, 16, 24, 16)
            cl.setSpacing(4)

            num_lbl = QLabel("0")
            num_lbl.setObjectName("SummaryNumber")
            caption_lbl = QLabel(title)
            caption_lbl.setObjectName("SummaryCaption")

            cl.addWidget(num_lbl, 0, Qt.AlignLeft)
            cl.addWidget(caption_lbl, 0, Qt.AlignLeft)

            setattr(self, attr_name, num_lbl)
            return card

        total_card   = make_summary_card("summary_total_label",   "Total Bags", "SummaryTotalCard")
        passed_card  = make_summary_card("summary_passed_label",  "Passed",     "SummaryPassedCard")
        failed_card  = make_summary_card("summary_failed_label",  "Failed",     "SummaryFailedCard")
        pending_card = make_summary_card("summary_pending_label", "Pending",    "SummaryPendingCard")

        summary_layout.addWidget(total_card)
        summary_layout.addWidget(passed_card)
        summary_layout.addWidget(failed_card)
        summary_layout.addWidget(pending_card)

        summary_outer.addLayout(summary_layout)

        scroll_layout.addWidget(self.summary_frame)
        self.summary_frame.hide()
        
        scroll_layout.addWidget(self.overall_frame)
        self.overall_frame.hide()

        # --- Save Inspection (no white card) ---
        save_container = QWidget()   # <-- NO SectionFrame
        save_row = QHBoxLayout(save_container)
        save_row.setContentsMargins(0, 0, 0, 0)
        save_row.addStretch()

        save_btn = QPushButton("Save Inspection")
        save_btn.setObjectName("SaveButton")
        save_btn.clicked.connect(self._on_save_inspection_clicked)
        save_row.addWidget(save_btn)

        scroll_layout.addWidget(save_container)
        self.save_section = save_container
        self.save_section.hide()


        # Connections
        self.carton_no_edit.returnPressed.connect(self.lookup_carton_from_db)
        
        # ✅ NEW: when Carton Number filled/scan → load Bag Numbers from com_bag_weight
        try:
            self.carton_no_edit.editingFinished.connect(self._refresh_bag_numbers_for_carton)
        except Exception:
            pass
        self.production_no_edit.returnPressed.connect(self.lookup_production_from_db)
        self.total_bags_edit.textChanged.connect(self.update_total_bags)
        self.total_bags_edit.textChanged.connect(self._update_bag_number_options)


        

        # ✅ NEW: when operator selects Bag Number → auto fill Unloading Operator
        try:
            self.bag_number_combo.currentIndexChanged.connect(self._on_bag_number_selected)
        except Exception:
            pass
# Auto-generate QC Number when window opens
        self._set_new_qc_number()

    def _update_bag_number_options(self):
        self.bag_number_combo.clear()
        self.bag_number_combo.addItem("Select Bag")

        try:
            total = int(self.total_bags_edit.text())
            for i in range(1, total + 1):
                self.bag_number_combo.addItem(str(i))
        except ValueError:
            pass
    # ====================== BAG NUMBER from com_bag_weight ======================
    def _fetch_bag_numbers_from_loop_dt_bag(self, carton_no: str):
        """Return unique bag_number list for this carton_no from gama_loop_dt_bag."""
        if not DB_ENABLED:
            return []

        carton_no = (carton_no or "").strip()
        if not carton_no:
            return []

        try:
            conn = mysql.connector.connect(
                host=DB_HOST,
                port=DB_PORT,
                user=DB_USER,
                password=DB_PASSWORD,
                database=DB_NAME,
                use_pure=True,
            )
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT bag_number
                FROM {DB_LOOP_DT_BAG}
                WHERE TRIM(carton_no)=TRIM(%s)
                AND bag_number IS NOT NULL
                AND TRIM(bag_number) <> ''
                ORDER BY id ASC
                """,
                (carton_no,),
            )
            rows = cur.fetchall() or []
            cur.close()
            conn.close()

            # de-duplicate while keeping order
            seen = set()
            out = []
            for r in rows:
                b = str(r[0]).strip()
                if b and b not in seen:
                    seen.add(b)
                    out.append(b)

            return out

        except Exception as e:
            print("Bag fetch error:", e)
            return []


    def _refresh_bag_numbers_for_carton(self):
        """Populate Bag Number dropdown based on com_bag_weight for the current carton."""
        carton_no = self.carton_no_edit.text().strip()

        # reset fields first
        try:
            self.unloading_operator_edit.clear()
        except Exception:
            pass

        bags = self._fetch_bag_numbers_from_loop_dt_bag(carton_no)
        if bags:
            self.bag_number_combo.blockSignals(True)
            try:
                self.bag_number_combo.clear()
                self.bag_number_combo.addItem("Select Bag")
                for b in bags:
                    self.bag_number_combo.addItem(b)
            finally:
                self.bag_number_combo.blockSignals(False)
        else:
            # fallback to original auto list (1..total bags)
            self._update_bag_number_options()

    def _fetch_unloading_operator_for_carton_bag(self, carton_no: str, bag_number: str) -> str:
        """Return unloading_operator for carton_no + bag_number from gama_loop_dt_bag (latest row)."""
        if not DB_ENABLED:
            return ""
        carton_no = (carton_no or "").strip()
        bag_number = (bag_number or "").strip()
        if not carton_no or not bag_number:
            return ""

        try:
            conn = mysql.connector.connect(
                host=DB_HOST,
                port=DB_PORT,
                user=DB_USER,
                password=DB_PASSWORD,
                database=DB_NAME,
                use_pure=True,
            )
            cur = conn.cursor()
            cur.execute(
                f"SELECT unloading_operator FROM {DB_LOOP_DT_BAG} "
                "WHERE TRIM(carton_no)=TRIM(%s) AND TRIM(bag_number)=TRIM(%s) "
                "ORDER BY id DESC LIMIT 1",
                (carton_no, bag_number),
            )
            row = cur.fetchone()
            cur.close()
            conn.close()
            return str(row[0] or "").strip() if row else ""
        except Exception:
            return ""


    def _on_bag_number_selected(self):
        """When user selects Bag Number → fill Unloading Operator."""
        try:
            if self.bag_number_combo.currentIndex() <= 0:
                self.unloading_operator_edit.clear()
                return
        except Exception:
            return

        carton_no = self.carton_no_edit.text().strip()
        bag_no = self.bag_number_combo.currentText().strip()
        if not bag_no or bag_no.lower() == "select bag":
            self.unloading_operator_edit.clear()
            return

        op = self._fetch_unloading_operator_for_carton_bag(carton_no, bag_no)
        try:
            self.unloading_operator_edit.setText(op)
        except Exception:
            pass



    def _generate_next_qc_no(self):
        """
        Look in gama_qc_carton_header and find last QC number for *today*,
        then return next one like QCTNIYYYYMMDD001.
        """
        today_str = datetime.now().strftime("%Y%m%d")
        prefix = f"QCTNI{today_str}"
        next_seq = 1  # default if no record yet

        if DB_ENABLED:
            try:
                conn = mysql.connector.connect(
                    host=DB_HOST,
                    port=DB_PORT,
                    user=DB_USER,
                    password=DB_PASSWORD,
                    database=DB_NAME,
                    use_pure=True,
                )
                cur = conn.cursor()
                # get latest qc_no for today: QCTNIYYYYMMDD###
                cur.execute(
                    f"SELECT qc_no FROM {DB_QC_HEADER} "
                    "WHERE qc_no LIKE %s "
                    "ORDER BY qc_no DESC LIMIT 1",
                    (prefix + "%",),
                )
                row = cur.fetchone()
                cur.close()
                conn.close()

                if row and row[0]:
                    last_qc = row[0]
                    # take last 3 digits
                    suffix = last_qc[-3:]
                    try:
                        last_seq = int(suffix)
                        next_seq = last_seq + 1
                    except ValueError:
                        next_seq = 1
            except Exception:
                # if DB error, just fall back to 001
                next_seq = 1

        return f"{prefix}{next_seq:03d}"

    def _set_new_qc_number(self):
        """Set QC Number field with auto-generated value and lock it."""
        new_qc = self._generate_next_qc_no()
        self.qc_no_edit.setText(new_qc)
        self.qc_no_edit.setReadOnly(True)   # user cannot change manually


    def _update_box_condition_styles(self):
        """Highlight the selected card similar to web UI."""
        # Base (unselected) style
        base_style = """
            #BoxCard {
                background-color: #FFFFFF;
                border: 1px solid #E5E7EB;
                border-radius: 8px;
            }
        """
        ok_selected = """
            #BoxCard {
                background-color: #ECFDF3;
                border: 2px solid #22C55E;
                border-radius: 8px;
            }
        """
        no_selected = """
            #BoxCard {
                background-color: #FEF2F2;
                border: 2px solid #EF4444;
                border-radius: 8px;
            }
        """

        # OK card
        if self.box_ok.isChecked():
            self.ok_card.setStyleSheet(ok_selected)
        else:
            self.ok_card.setStyleSheet(base_style)

        # NO card
        if self.box_no.isChecked():
            self.no_card.setStyleSheet(no_selected)
        else:
            self.no_card.setStyleSheet(base_style)

    def _open_weight_dialog(self, weight_edit,card):
        """Open the Digital Weight Scale popup for this bag."""
        dlg = DigitalWeightDialog(self, weight_edit,card)
        dlg.exec()


    def _create_bag_cards(self):
        for i in range(self.total_bags):
            card = self._create_bag_card(i + 1)
            row = i // 3
            col = i % 3
            self.bag_grid.addWidget(card, row, col)
            self.bag_cards.append(card)

    def _reset_bag_visibility(self):
        """Show only current bag (start with Bag #1)."""
        self.current_bag_index = 0          # start on Bag #1
        for i, card in enumerate(self.bag_cards):
            if i == 0:
                card.show()                 # show Bag #1
            else:
                card.hide()                 # hide Bag #2, #3, ...

    def _create_bag_card(self, num):
        card = QFrame()
        card.setObjectName("BagCard")
        card._bag_index = num  # ✅ remember which bag this card is
        layout = QVBoxLayout(card)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        title = QLabel(f"Bag #{num}")
        title.setObjectName("BagTitle")
        layout.addWidget(title)

        # ✅ NEW: show Unloading Operator + Bag Number (e.g. "ADDY" and "2/9") under Bag title
        meta_row = QHBoxLayout()
        meta_row.setSpacing(12)

        op_meta = QLabel("--")
        op_meta.setObjectName("BagOpMeta")
        op_meta.setStyleSheet("color:#374151;font-weight:600;")

        bag_meta = QLabel("")
        bag_meta.setObjectName("BagNoMeta")
        bag_meta.setStyleSheet("color:#6B7280;font-weight:600;")

        meta_row.addWidget(op_meta)
        meta_row.addStretch()
        meta_row.addWidget(bag_meta)

        meta_wrap = QWidget()
        meta_wrap.setLayout(meta_row)
        layout.addWidget(meta_wrap)

        # store refs so DigitalWeightDialog can update after OK
        card.bag_op_meta = op_meta
        card.bag_no_meta = bag_meta
        card.bag_index = num


        status = QLabel("Pending")
        status.setObjectName("StatusLabel")
        layout.addWidget(status, alignment=Qt.AlignRight)

        # Row: Weight field + blue scale button
        weight_row = QHBoxLayout()

        weight_edit = QLineEdit()
        weight_edit.setPlaceholderText("Weight (Kg) *")
        weight_edit.textChanged.connect(lambda text, c=card: self._on_weight_changed(c, text))
        weight_row.addWidget(weight_edit)

        weight_btn = QPushButton("")
        weight_btn.setObjectName("WeightButton")
        weight_btn.setFixedWidth(40)
        weight_btn.setMinimumHeight(40)
        # When click → open Digital Weight Scale dialog
        weight_btn.clicked.connect(
            lambda _, e=weight_edit, c=card: self._open_weight_dialog(e, c)
        )

        weight_row.addWidget(weight_btn)

        layout.addLayout(weight_row)

        # store reference so we can auto-open scale for this bag
        card.weight_edit = weight_edit


        # === 1st ROW : Metal + Label (side by side) ===
        row1 = QHBoxLayout()

        metal_group = QGroupBox("Metal Detector Test *")
        metal_group.setObjectName("MetalGroup")      # <-- add this line
        metal_layout = QVBoxLayout(metal_group)
        metal_ok = QRadioButton("OK")
        metal_no = QRadioButton("No")
        metal_layout.addWidget(metal_ok)
        metal_layout.addWidget(metal_no)
        # 🔒 view-only on card
        metal_ok.setEnabled(False)
        metal_no.setEnabled(False)

        label_group = QGroupBox("Label *")
        label_group.setObjectName("LabelGroup")      # <-- add this line
        label_layout = QVBoxLayout(label_group)
        label_ok = QRadioButton("OK")
        label_no = QRadioButton("No")
        label_layout.addWidget(label_ok)
        label_layout.addWidget(label_no)
        # 🔒 view-only on card
        label_ok.setEnabled(False)
        label_no.setEnabled(False)

        row1.addWidget(metal_group)
        row1.addWidget(label_group)
        layout.addLayout(row1)


        # === 2nd ROW : Dirty + Plastic (side by side) ===
        row2 = QHBoxLayout()

        dirty_group = QGroupBox("Dirty *")
        dirty_group.setObjectName("DirtyGroup")      # <-- add this line
        dirty_layout = QVBoxLayout(dirty_group)
        dirty_yes = QRadioButton("Yes")
        dirty_no = QRadioButton("No")
        dirty_layout.addWidget(dirty_yes)
        dirty_layout.addWidget(dirty_no)
        # 🔒 view-only on card
        dirty_yes.setEnabled(False)
        dirty_no.setEnabled(False)

        plastic_group = QGroupBox("Plastic *")
        plastic_group.setObjectName("PlasticGroup")  # <-- add this line
        plastic_layout = QVBoxLayout(plastic_group)
        plastic_ok = QRadioButton("OK")
        plastic_no = QRadioButton("No")
        plastic_layout.addWidget(plastic_ok)
        plastic_layout.addWidget(plastic_no)
        # 🔒 view-only on card
        plastic_ok.setEnabled(False)
        plastic_no.setEnabled(False)

        row2.addWidget(dirty_group)
        row2.addWidget(plastic_group)
        layout.addLayout(row2)

        remarks = QTextEdit()
        remarks.setPlaceholderText("Any remarks...")
        remarks.setFixedHeight(50)
        layout.addWidget(remarks)

        status_group = QGroupBox("Final Status *")
        status_group.setObjectName("FinalStatusGroup")   # <-- add this line
        status_layout = QHBoxLayout(status_group)

        status_pass = QRadioButton("Pass")
        status_pass.setObjectName("StatusPassRadio")   # <- NEW

        status_fail = QRadioButton("Fail")
        status_fail.setObjectName("StatusFailRadio")   # <- NEW

        # 🔒 make Final Status view-only on the card
        status_pass.setEnabled(False)
        status_fail.setEnabled(False)

        status_layout.addWidget(status_pass)
        status_layout.addWidget(status_fail)
        layout.addWidget(status_group)

                # --- Update top status label when Pass/Fail clicked ---
        # --- Update top status label when Pass/Fail clicked ---
        def update_final_status():
            top_status = card.findChild(QLabel, "StatusLabel")

            if status_pass.isChecked():
                top_status.setText("Passed")
                top_status.setStyleSheet(
                    "background:#d1fae5; color:#065f46; padding:4px 12px; "
                    "border-radius:20px; font-size:12px;"
                )
            elif status_fail.isChecked():
                top_status.setText("Failed")
                top_status.setStyleSheet(
                    "background:#fee2e2; color:#b91c1c; padding:4px 12px; "
                    "border-radius:20px; font-size:12px;"
                )
            else:
                top_status.setText("Pending")
                top_status.setStyleSheet(
                    "background:#fef3c7; color:#92400e; padding:4px 12px; "
                    "border-radius:20px; font-size:12px;"
                )

            # update summary/progress numbers
            self.update_progress()

            # 🔹 if this bag is Passed/Failed and it's the current bag,
            #     auto-show the next bag card
            if top_status.text() in ("Passed", "Failed"):
                try:
                    idx = self.bag_cards.index(card)
                except ValueError:
                    idx = -1

                if idx == self.current_bag_index:
                    next_idx = idx + 1
                    if next_idx < len(self.bag_cards):
                        self.current_bag_index = next_idx
                        next_card = self.bag_cards[next_idx]
                        next_card.show()

                        # scroll so operator sees next bag
                        if self.scroll_area:
                            self.scroll_area.ensureWidgetVisible(next_card)

                        # 🔹 Auto-open Digital Weight Scale for the next bag (delay so UI can repaint)
                        weight_edit_next = getattr(next_card, "weight_edit", None)
                        if weight_edit_next is not None:
                            def _open_next():
                                try:
                                    self._open_weight_dialog(weight_edit_next, next_card)
                                except Exception:
                                    pass

                            # ✅ delay a bit so Bag #1 meta text updates immediately
                            QTimer.singleShot(200, _open_next)


        # connect radios so changing them updates status + moves to next bag
        status_pass.toggled.connect(update_final_status)
        status_fail.toggled.connect(update_final_status)

        # set initial pill + summary once
        update_final_status()

        return card

    def _on_save_inspection_clicked(self):
        """
        Save current carton inspection into:
          - gama_qc_carton_header
          - gama_qc_carton_bag
        """
        # --- Basic validation ---
        if not self.qc_no_edit.text().strip():
            QMessageBox.warning(self, "Missing QC No", "Please enter QC Number.")
            return
        if not self.carton_no_edit.text().strip():
            QMessageBox.warning(self, "Missing Carton", "Please enter Carton Number.")
            return
        if not self.production_no_edit.text().strip():
            QMessageBox.warning(self, "Missing Production No", "Please enter Production Number.")
            return
        if not self.operator_name_edit.text().strip():
            QMessageBox.warning(self, "Missing Operator", "Please enter Operator Name.")
            return
        if not self.checked_by_edit.text().strip():
            QMessageBox.warning(self, "Missing Checked By", "Please enter Checked By.")
            return
        if not self.unloading_operator_edit.text().strip():
            QMessageBox.warning(self,"Missing Unloading Operator","Please enter Unloading Operator.")
            return
        if self.bag_number_combo.currentIndex() == 0:
            QMessageBox.warning(self,"Missing Bag Number","Please select Bag Number.")
            return


        # Overall result must be selected
        if not (self.overall_pass.isChecked() or self.overall_fail.isChecked()):
            QMessageBox.warning(
                self,
                "Missing QC Final Result",
                "Please choose QC Final Result (PASS or FAIL)."
            )
            return

        # --- Count Passed / Failed / Pending from bag cards ---
        passed = failed = pending = 0
        for card in self.bag_cards:
            status_lbl = card.findChild(QLabel, "StatusLabel")
            if not status_lbl:
                continue
            txt = status_lbl.text()
            if txt == "Passed":
                passed += 1
            elif txt == "Failed":
                failed += 1
            else:
                pending += 1

        # --- Collect header values from UI ---
        qc_no         = self.qc_no_edit.text().strip()
        check_by      = self.checked_by_edit.text().strip()
        carton_no     = self.carton_no_edit.text().strip()
        production_no = self.production_no_edit.text().strip()
        operator_name = self.operator_name_edit.text().strip()
        loop_size     = self.loop_size_view_edit.text().strip() or None

        def to_int(txt):
            txt = (txt or "").strip()
            return int(txt) if txt else None

        def to_dec(txt):
            txt = (txt or "").strip()
            return float(txt) if txt else None

        pcs_per_bag = to_int(self.pcs_per_bag_edit.text())
        total_bags  = to_int(self.total_bags_edit.text())
        min_w       = to_dec(self.min_weight_edit.text())
        avg_w       = to_dec(self.avg_weight_edit.text())
        max_w       = to_dec(self.max_weight_edit.text())

        if self.box_ok.isChecked():
            box_condition = "OK"
        elif self.box_no.isChecked():
            box_condition = "NO"
        else:
            box_condition = None

        box_remarks = self.box_remarks.toPlainText().strip() or None

        if self.overall_pass.isChecked():
            overall_result = "PASS"
        elif self.overall_fail.isChecked():
            overall_result = "FAIL"
        else:
            overall_result = None

        # --- Connect to MySQL and insert header + bags ---
        try:
            conn = mysql.connector.connect(
                host=DB_HOST,
                port=DB_PORT,
                user=DB_USER,
                password=DB_PASSWORD,
                database=DB_NAME,
                use_pure=True,
            )
            cur = conn.cursor()

            # 1) Insert into gama_qc_carton_header
            sql_header = f"""
            INSERT INTO {DB_QC_HEADER}
            (qc_no, check_by, carton_no, production_no, operator_name,
            loop_size, pcs_per_bag, total_bags,
            min_weight, avg_weight, max_weight,
            box_condition, box_remarks,
            overall_result,
            total_passed, total_failed, total_pending,
            started_at, completed_at,
            created_at)
            VALUES
            (%s,%s,%s,%s,%s,
            %s,%s,%s,
            %s,%s,%s,
            %s,%s,
            %s,
            %s,%s,%s,
            %s,%s,
            NOW())
            """

            from datetime import datetime
            completed_at = datetime.now()
            started_at = getattr(self, "inspection_started_at", None) or completed_at

            started_at = getattr(self, "inspection_started_at", datetime.now())
            completed_at = datetime.now()

            header_data = (
                qc_no, check_by, carton_no, production_no, operator_name,
                loop_size, pcs_per_bag, total_bags,
                min_w, avg_w, max_w,
                box_condition, box_remarks,
                overall_result,
                passed, failed, pending,
                started_at,
                completed_at
            )


            cur.execute(sql_header, header_data)
            header_id = cur.lastrowid

            # 2) Insert each bag into gama_qc_carton_bag
            sql_bag = f"""
                INSERT INTO {DB_QC_BAG}
                (header_id, qc_no, carton_no,
                 bag_no, bag_number, unloading_operator,
                 weight_kg,
                 metal_result, label_result, dirty_result, plastic_result,
                 final_status, remarks, created_at)
                VALUES
                (%s,%s,%s,
                 %s,%s,%s,
                 %s,
                 %s,%s,%s,%s,
                 %s,%s, NOW())
            """

            for idx, card in enumerate(self.bag_cards, start=1):
                # Weight
                weight_edit = card.findChild(QLineEdit)
                weight_val = to_dec(weight_edit.text()) if weight_edit else None

                # Metal group
                metal_group = card.findChild(QGroupBox, "MetalGroup")
                metal_res = None
                if metal_group:
                    metal_ok = metal_group.layout().itemAt(0).widget()
                    metal_no = metal_group.layout().itemAt(1).widget()
                    if metal_ok.isChecked():
                        metal_res = "OK"
                    elif metal_no.isChecked():
                        metal_res = "NO"

                # Label group
                label_group = card.findChild(QGroupBox, "LabelGroup")
                label_res = None
                if label_group:
                    label_ok = label_group.layout().itemAt(0).widget()
                    label_no = label_group.layout().itemAt(1).widget()
                    if label_ok.isChecked():
                        label_res = "OK"
                    elif label_no.isChecked():
                        label_res = "NO"

                # Dirty group
                dirty_group = card.findChild(QGroupBox, "DirtyGroup")
                dirty_res = None
                if dirty_group:
                    dirty_yes = dirty_group.layout().itemAt(0).widget()
                    dirty_no = dirty_group.layout().itemAt(1).widget()
                    if dirty_yes.isChecked():
                        dirty_res = "YES"
                    elif dirty_no.isChecked():
                        dirty_res = "NO"

                # Plastic group
                plastic_group = card.findChild(QGroupBox, "PlasticGroup")
                plastic_res = None
                if plastic_group:
                    plastic_ok = plastic_group.layout().itemAt(0).widget()
                    plastic_no = plastic_group.layout().itemAt(1).widget()
                    if plastic_ok.isChecked():
                        plastic_res = "OK"
                    elif plastic_no.isChecked():
                        plastic_res = "NO"

                # Final status text: "Passed" / "Failed" / "Pending"
                status_lbl = card.findChild(QLabel, "StatusLabel")
                final_status = status_lbl.text() if status_lbl else None

                # Remarks
                remarks_edit = card.findChild(QTextEdit)
                bag_remarks = remarks_edit.toPlainText().strip() if remarks_edit else ""
                bag_remarks = bag_remarks or None

                # ✅ NEW: per-card Bag Number + Unloading Operator (saved from Digital Scale)
                bag_number_val = None
                unloading_operator_val = None
                try:
                    if hasattr(card, "_bag_number_display"):
                        t = str(getattr(card, "_bag_number_display") or "").strip()
                        bag_number_val = t if t else None
                except Exception:
                    pass
                try:
                    if bag_number_val is None and hasattr(card, "bag_no_meta"):
                        t = str(card.bag_no_meta.text() or "").strip()
                        bag_number_val = t if t else None
                except Exception:
                    pass

                try:
                    if hasattr(card, "_unloading_operator"):
                        t = str(getattr(card, "_unloading_operator") or "").strip()
                        unloading_operator_val = t if t else None
                except Exception:
                    pass
                try:
                    if unloading_operator_val is None and hasattr(card, "bag_op_meta"):
                        t = str(card.bag_op_meta.text() or "").strip()
                        unloading_operator_val = t if (t and t != "--") else None
                except Exception:
                    pass

                bag_data = (
                    header_id, qc_no, carton_no,
                    idx, bag_number_val, unloading_operator_val,
                    weight_val,
                    metal_res, label_res, dirty_res, plastic_res,
                    final_status, bag_remarks,
                )
                cur.execute(sql_bag, bag_data)

            conn.commit()
            cur.close()
            conn.close()

            QMessageBox.information(
                self,
                "Saved",
                f"Inspection saved.\nHeader ID: {header_id}"
            )

            # 🔄 Reset everything for next carton
            self.new_carton()

            # Reset bag cards section
            self.bag_section.hide()
            self.condition_frame.hide()

            # Scroll back to top (QC Number)
            if self.scroll_area:
                self.scroll_area.verticalScrollBar().setValue(0)


            # Optionally reset for next carton
            # self.new_carton()

        except Exception as e:
            QMessageBox.critical(
                self,
                "Database Error",
                f"Failed to save inspection to MySQL:\n{e}"
            )



    def update_total_bags(self, text):
        try:
            new_total = int(text)
            if new_total != self.total_bags:
                self.total_bags = new_total

                # remove old cards
                for card in self.bag_cards:
                    card.setParent(None)
                self.bag_cards = []

                # create new cards
                self._create_bag_cards()

                # 🔹 After recreating bags, show only Bag #1
                self._reset_bag_visibility()

                # update "0 / X Bags" text
                self.update_progress()

        except ValueError:
            # ignore invalid input
            pass

    def _on_weight_changed(self, card, text):
        """Called whenever the Weight(Kg) field of a bag card changes."""
        text = text.strip()

        if text:
            # --- 1) Try read current weight value ---
            try:
                weight_val = float(text)
            except ValueError:
                # Not a number → treat as pending
                status = card.findChild(QLabel, "StatusLabel")
                if status:
                    status.setText("Pending")
                    status.setStyleSheet(
                        "background:#fef3c7; color:#92400e; padding:4px 12px; "
                        "border-radius:20px; font-size:12px;"
                    )
                return


            label_group = card.findChild(QGroupBox, "LabelGroup")
            if label_group:
                label_ok = label_group.layout().itemAt(0).widget()
                label_no = label_group.layout().itemAt(1).widget()
                label_no.setChecked(False)

            plastic_group = card.findChild(QGroupBox, "PlasticGroup")
            if plastic_group:
                plastic_ok = plastic_group.layout().itemAt(0).widget()
                plastic_no = plastic_group.layout().itemAt(1).widget()
                plastic_no.setChecked(False)

            # just update progress; weight should NOT auto-jump to next bag
            self.update_progress()


        else:
            # === Weight box emptied → reset everything to Pending ===
            metal_group = card.findChild(QGroupBox, "MetalGroup")
            if metal_group:
                metal_pass = metal_group.layout().itemAt(0).widget()
                metal_fail = metal_group.layout().itemAt(1).widget()
                metal_pass.setChecked(False)
                metal_fail.setChecked(False)

            dirty_group = card.findChild(QGroupBox, "DirtyGroup")
            if dirty_group:
                dirty_yes = dirty_group.layout().itemAt(0).widget()
                dirty_no = dirty_group.layout().itemAt(1).widget()
                dirty_yes.setChecked(False)
                dirty_no.setChecked(False)

            # NEW: clear Label radios
            label_group = card.findChild(QGroupBox, "LabelGroup")
            if label_group:
                label_ok = label_group.layout().itemAt(0).widget()
                label_no = label_group.layout().itemAt(1).widget()
                label_ok.setChecked(False)
                label_no.setChecked(False)

            # NEW: clear Plastic radios
            plastic_group = card.findChild(QGroupBox, "PlasticGroup")
            if plastic_group:
                plastic_ok = plastic_group.layout().itemAt(0).widget()
                plastic_no = plastic_group.layout().itemAt(1).widget()
                plastic_ok.setChecked(False)
                plastic_no.setChecked(False)

            # Reset top pill
            status = card.findChild(QLabel, "StatusLabel")
            if status:
                status.setText("Pending")
                status.setStyleSheet(
                    "background:#fef3c7; color:#92400e; padding:4px 12px; "
                    "border-radius:20px; font-size:12px;"
                )

            self.update_progress()


    def update_progress(self):
        passed = 0
        failed = 0
        pending = 0

        for card in self.bag_cards:
            status_lbl = card.findChild(QLabel, "StatusLabel")
            if not status_lbl:
                continue
            txt = status_lbl.text()
            if txt == "Passed":
                passed += 1
            elif txt == "Failed":
                failed += 1
            else:
                pending += 1

        total = self.total_bags

        # top-right text near "Inspection Progress"
        self.progress_status.setText(f"{passed} / {total} Bags")

        # update summary tiles (if they exist)
        if hasattr(self, "summary_total_label"):
            self.summary_total_label.setText(str(total))
            self.summary_passed_label.setText(str(passed))
            self.summary_failed_label.setText(str(failed))
            self.summary_pending_label.setText(str(pending))

    def start_inspection(self):
        from datetime import datetime

        # ✅ prevent overwrite if user clicks Start twice
        if getattr(self, "inspection_started_at", None) is None:
            self.inspection_started_at = datetime.now()

        # Validate fields
        if not self.carton_no_edit.text().strip() or \
        not self.production_no_edit.text().strip() or \
        not self.operator_name_edit.text().strip() or \
        not self.qc_no_edit.text().strip() or \
        not self.total_bags_edit.text().strip():
            QMessageBox.warning(
                self, "Missing Info",
                "Please fill in all required Carton Box Information fields."
            )
            return

        # Show condition section
        self.condition_frame.show()

        # Smooth scroll to box condition
        if self.scroll_area:
            self.scroll_area.ensureWidgetVisible(self.condition_frame)



    def proceed_to_bags(self):
        # Optionally enforce that a condition is chosen (OK / No)
        if not (self.box_ok.isChecked() or self.box_no.isChecked()):
            QMessageBox.warning(
                self,
                "Missing Selection",
                "Please select the carton box condition (OK or No) before proceeding."
            )
            return

        # 🔴 Hide the carton box condition section
        self.condition_frame.hide()

        # Show bag inspection section
        self.bag_section.show()
        # Reset QC Final Result (overall PASS / FAIL)
        if hasattr(self, "overall_pass") and hasattr(self, "overall_fail"):
            self.overall_pass.setChecked(False)
            self.overall_fail.setChecked(False)
            self.update_overall_style()
        # Show QC Final Result + Summary + Save card
        if hasattr(self, "overall_frame"):
            self.overall_frame.show()
        if hasattr(self, "summary_frame"):
            self.summary_frame.show()
        if hasattr(self, "save_section"):
            self.save_section.show()

        # Auto-scroll to bag section
        if self.scroll_area:
            self.scroll_area.ensureWidgetVisible(self.bag_section)


        # Scroll down to the bag section
        if self.scroll_area:
            self.scroll_area.ensureWidgetVisible(self.bag_section)

        # 🔹 Auto-open Digital Weight Scale for Bag #1
        if self.bag_cards:
            first_card = self.bag_cards[0]
            weight_edit = getattr(first_card, "weight_edit", None)
            if weight_edit is not None:
                self._open_weight_dialog(weight_edit, first_card)


    def new_carton(self):
        # Clear top / header fields
        self.carton_no_edit.clear()
        self.production_no_edit.clear()
        self.operator_name_edit.clear()

        self.loop_size_view_edit.clear()
        self.pcs_per_bag_edit.clear()
        self.total_bags_edit.clear()
        self.box_remarks.clear()

        self.min_weight_edit.clear()
        self.avg_weight_edit.clear()
        self.max_weight_edit.clear()

        # Reset QC Final Result (overall PASS / FAIL)
        if hasattr(self, "overall_pass") and hasattr(self, "overall_fail"):
            self.overall_pass.setChecked(False)
            self.overall_fail.setChecked(False)
            self.update_overall_style()

        # 🔥 NEW: hide QC Final Result + Summary + Save row
        if hasattr(self, "overall_frame"):
            self.overall_frame.hide()
        if hasattr(self, "summary_frame"):
            self.summary_frame.hide()
        if hasattr(self, "save_section"):
            self.save_section.hide()

        # Reset box condition to no selection
        self.box_group.setExclusive(False)
        self.box_ok.setChecked(False)
        self.box_no.setChecked(False)
        self.box_group.setExclusive(True)
        self._update_box_condition_styles()

        # 🔥 HARD RESET BAG CARDS: remove all from grid + reset list
        if hasattr(self, "bag_grid"):
            for i in reversed(range(self.bag_grid.count())):
                item = self.bag_grid.itemAt(i)
                w = item.widget()
                if w is not None:
                    w.setParent(None)
                self.bag_grid.removeItem(item)

        # reset internal tracking
        self.bag_cards = []
        self.current_bag_index = 0
        self.total_bags = 0

        # Reset progress + summary numbers
        self.update_progress()

        # Generate next QC Number for new carton
        self._set_new_qc_number()



    def lookup_carton_from_db(self):
        if not DB_ENABLED:
            return
        carton = self.carton_no_edit.text().strip()
        if not carton:
            return
        try:
            conn = mysql.connector.connect(
                host=DB_HOST,
                port=DB_PORT,
                user=DB_USER,
                password=DB_PASSWORD,
                database=DB_NAME,
                use_pure=True,
            )
            cur = conn.cursor()
            cur.execute(f"SELECT operators, production_no FROM {DB_TABLE} WHERE carton_no=%s ORDER BY id DESC LIMIT 1", (carton,))
            row = cur.fetchone()
            cur.close()
            conn.close()
            if row:
                self.operator_name_edit.setText(str(row[0] or ""))
                self.production_no_edit.setText(str(row[1] or ""))
                self.lookup_production_from_db(row[1])



                # ✅ NEW: load Bag Numbers for this carton from com_bag_weight
                try:
                    self._refresh_bag_numbers_for_carton()
                except Exception:
                    pass
        except Exception as e:
            QMessageBox.critical(self, "DB Error", f"Carton lookup failed:\n{e}")

    def lookup_production_from_db(self, prod_no=None):
        if not DB_ENABLED:
            return
        if prod_no is None:
            prod_no = self.production_no_edit.text().strip()
        if not prod_no:
            return
        try:
            conn = mysql.connector.connect(
                host=DB_HOST,
                port=DB_PORT,
                user=DB_USER,
                password=DB_PASSWORD,
                database=DB_NAME,
                use_pure=True,
            )
            cur = conn.cursor()
            cur.execute(
                f"SELECT loop_size, pcs_bag, colour, min_weight, max_weight, weight_bag, bags_box "
                f"FROM {DB_TABLE_PROD} "
                f"WHERE production_no=%s "
                f"ORDER BY production_date DESC LIMIT 1",
                (prod_no,)
            )

            row = cur.fetchone()
            cur.close()
            conn.close()
            if row:
                loop_size, pcs_bag, colour, min_w, max_w, avg_w, bags_box = row
                self.loop_size_view_edit.setText(f"{loop_size or ''} {colour or ''}".strip())
                self.pcs_per_bag_edit.setText(str(pcs_bag or ""))
                # Set weight limits into the header fields
                self.min_weight_edit.setText(str(min_w or ""))
                self.avg_weight_edit.setText(str(avg_w or ""))
                self.max_weight_edit.setText(str(max_w or ""))
                # 🔹 NEW: total bags in carton from bags_box
                self.total_bags_edit.setText(str(bags_box or ""))

        except Exception as e:
            QMessageBox.critical(self, "DB Error", f"Production lookup failed:\n{e}")


    def set_overall_status(self, is_pass):
        if is_pass:
            self.overall_pass.setChecked(True)
            self.overall_fail.setChecked(False)
        else:
            self.overall_pass.setChecked(False)
            self.overall_fail.setChecked(True)

        # update styles
        self.update_overall_style()

    def update_overall_style(self):
        if self.overall_pass.isChecked():
            self.overall_pass.setStyleSheet("background:#C8F5C8; border:1px solid #9EDB9E;")
            self.overall_fail.setStyleSheet("background:#FFFFFF; border:1px solid #CCCCCC;")
        elif self.overall_fail.isChecked():
            self.overall_fail.setStyleSheet("background:#F6C8C8; border:1px solid #E89A9A;")
            self.overall_pass.setStyleSheet("background:#FFFFFF; border:1px solid #CCCCCC;")
        else:
            # no selection
            self.overall_pass.setStyleSheet("background:#FFFFFF; border:1px solid #CCCCCC;")
            self.overall_fail.setStyleSheet("background:#FFFFFF; border:1px solid #CCCCCC;")


    def _apply_styles(self):
        self.setStyleSheet("""
            QWidget {
                background-color: transparent;
                font-family: "Segoe UI", Arial, sans-serif;
                font-size: 14pt;
                color: #1F2937;
            }
            QLabel {
                background: transparent;
            }

            /* Make warning / info popups look professional */
            QMessageBox {
                background-color: #FFFFFF;   /* white popup */
            }
            QMessageBox QLabel {
                color: #111827;              /* dark text */
                background: transparent;
                font-size: 11pt;
            }
            QMessageBox QPushButton {
                background-color: #2563EB;   /* blue OK button */
                color: #FFFFFF;
                border-radius: 6px;
                padding: 6px 16px;
                min-width: 70px;
            }
            QMessageBox QPushButton:hover {
                background-color: #1D4ED8;   /* darker on hover */
            }

            #TitleLabel {
                color: #3b82f6;
                font-size: 20pt;
                font-weight: bold;
            }
            #SubtitleLabel {
                color: #64748b;
                font-size: 14pt;
            }
            #DateLabel {
                color: #FFFFFF;     /* White text */
                font-size: 15pt;
                font-weight: 600;   /* optional, to make nicer */
            }
            #SectionFrame {
                background-color: #FFFFFF;
                border-radius: 8px;
                border: 1px solid #E5E7EB;

            }
            #SectionTitle {
                color: #1e293b;
                font-size: 16pt;
                font-weight: bold;
            }
            #FieldLabel {
                color: #374151;
                font-size: 12pt;
                font-weight: 600;
            }
            QLineEdit, QTextEdit, QComboBox {
                background-color: #FFFFFF;
                border: 1px solid #D1D5DB;
                border-radius: 4px;
                padding: 8px 12px;
                min-height: 36px;
            }
            QLineEdit:focus, QTextEdit:focus, QComboBox:focus {
                border: 2px solid #3b82f6;
                outline: none;
            }
            QRadioButton {
                spacing: 8px;
                font-size: 12pt;
                color: #1f2937;
            }
            #StatusPassRadio {
                color: #16A34A;        /* green */
                font-weight: 600;
            }
            #StatusFailRadio {
                color: #DC2626;        /* red */
                font-weight: 600;
            }
            QRadioButton::indicator {
                width: 18px;
                height: 18px;
                border: 1px solid #9ca3af;
                border-radius: 9px;
            }
            /* ----- Radio buttons (all places) ----- */
            QRadioButton {
                spacing: 8px;
                font-size: 12pt;
                color: #1f2937;
            }

            /* Base circle for ALL radios (enabled) */
            QRadioButton::indicator {
                width: 18px;
                height: 18px;
                border-radius: 9px;
                border: 1px solid #9ca3af;
                background: #FFFFFF;
            }

            /* When a normal radio is checked → dark dot */
            QRadioButton::indicator:checked {
                background-color: #111827;
                border: 1px solid #111827;
            }

            /* Disabled radios (like view-only on bag card) */
            QRadioButton::indicator:disabled {
                border: 1px solid #D1D5DB;
                background: #F9FAFB;
            }
            /* Disabled but CHECKED → show dark dot (for bag card view-only radios) */
            QRadioButton::indicator:disabled:checked {
                background-color: #111827;
                border: 1px solid #111827;
            }
            /* ----- SPECIAL: Box Condition OK / No dots ----- */
            #BoxOkRadio::indicator:checked {
                background-color: #22C55E;   /* green */
                border: 1px solid #16A34A;
            }

            #BoxNoRadio::indicator:checked {
                background-color: #EF4444;   /* red */
                border: 1px solid #B91C1C;
            }
                           
            /* ----- SPECIAL: Final Status dots in bag card ----- */
            #StatusPassRadio {
                color: #16A34A;        /* green text */
                font-weight: 600;
            }
            #StatusFailRadio {
                color: #DC2626;        /* red text */
                font-weight: 600;
            }

            /* PASS checked → GREEN DOT */
            #StatusPassRadio::indicator:checked {
                background-color: #22C55E;
                border: 1px solid #22C55E;
            }

            /* FAIL checked → RED DOT */
            #StatusFailRadio::indicator:checked {
                background-color: #EF4444;
                border: 1px solid #EF4444;
            }

            #StartButton {
                background-color: #3b82f6;
                color: white;
                font-weight: 600;
                border-radius: 6px;
                padding: 10px 20px;
            }
            /* ----- QC Final Result Purple Card ----- */
            #OverallResultFrame {
                background-color: #F5F3FF;   /* Purple card */
                border-radius: 12px;
                border: 1px solid #000000;
                padding: 16px;
            }

            /* Black label text */
            #OverallLabel {
                color: #000000;
                font-size: 25pt;
                font-weight: 600;
            }
            

            /* PASS / FAIL buttons inside purple card */
            QPushButton#OverallPassButton,
            QPushButton#OverallFailButton {
                padding: 10px 24px;
                border-radius: 8px;
                font-weight: 600;
                background: #FFFFFF;
                color: #4B5563;
                border: none;
            }

            QPushButton#OverallPassButton:checked {
                background: #22C55E;
                color: white;
            }
            QPushButton#OverallFailButton:checked {
                background: #EF4444;
                color: white;
            }

            #ProceedButton {
                background-color: #22c55e;
                color: white;
                font-weight: 600;
                border-radius: 6px;
                padding: 10px 20px;
            }
            #SaveButton {
                background-color: #3b82f6;
                color: white;
                font-weight: 600;
                border-radius: 10px;
                min-width: 220px;
                min-height: 60px;
                font-size: 25pt;
                padding: 14px 28px;
            }
            #NewCartonButton {
                background-color: #1f2937;
                color: white;
                font-weight: 600;
                border-radius: 6px;
                padding: 10px 20px;
            }
            #BagCard {
                background-color: #FFFFFF;
                border-radius: 10px;
                border: 1px  solid #94A3B8;;
            }
            #BagTitle {
                font-size: 14pt;
                font-weight: 600;
                color: #1e293b;
            }
            #StatusLabel {
                background-color: #fef3c7;
                color: #92400e;
                padding: 4px 12px;
                border-radius: 999px;
                font-size: 10pt;
                font-weight: 500;
            }
            QGroupBox {
                font-size: 12pt;
                font-weight: 600;
                color: #1e293b;
            }
            #ProgressTitle {
                font-size: 18pt;
                font-weight: 700;
                color: #1e293b;
            }
            /* Box Condition Titles (Green & Red) */
            #BoxOkTitle {
                color: #16A34A;      /* green */
                font-size: 14pt;
                font-weight: 600;
                background-color: transparent;
                border: none;
            }
            #BoxNoTitle {
                color: #DC2626;      /* red */
                font-size: 14pt;
                font-weight: 600;
                background-color: transparent;
                border: none;
            }
            #BoxSubtitle {
                color: #374151;
                font-size: 12pt;
                border: none;
                background: transparent;
            }
            #SubtitleFrame {
                background: transparent;
                border: none;
            }
            #WeightButton {
                background-color: #2563EB;
                border-radius: 6px;
                border: none;
            }
            #WeightButton:hover {
                background-color: #1D4ED8;
            }
            #ScaleDialog {
                background: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 #FFFFFF,     /* top pure white */
                    stop:1 #EBF2FF      /* bottom light blue */
                );
                border: 2px solid black;
                border-radius: 12px;
            }
            #ScaleLockButton {
                background-color: #111827;   /* dark */
                color: #FFFFFF;
                font-weight: 600;
                border-radius: 8px;
                border: none;
            }
            #ScaleLockButton:hover {
                background-color: #0B1220;
            }
            #ScaleLockButton:checked {
                background-color: #16A34A;   /* green when locked */
                color: #FFFFFF;
            }


            #ScaleDisplayFrame {
                background-color: #020617;
                border-radius: 16px;
            }
            #ScaleWeightLabel {
                color: #22C55E;
                font-size: 24pt;
                font-weight: 600;
            }
            #ScaleOkButton {
                background-color: #2563EB;
                color: #FFFFFF;
                font-weight: 600;
                border-radius: 8px;
                border: none;
            }
            #ScaleOkButton:hover {
                background-color: #1D4ED8;
            }
            #ScaleResetButton {
                background-color: #DC2626;
                color: #FFFFFF;
                font-weight: 600;
                border-radius: 8px;
                border: none;
            }
            #ScaleResetButton:hover {
                background-color: #B91C1C;
            }
            /* ----- Inspection Summary section ----- */
            #SummaryFrame {
                background-color: #FFFFFF;
                border-radius: 8px;
                border: 1px solid #E5E7EB;
            }
            #SummarySectionTitle {
                font-size: 13pt;
                font-weight: 600;
                color: #111827;
            }
            #SummaryIcon {
                font-size: 12pt;
            }

            #SummaryTotalCard,
            #SummaryPassedCard,
            #SummaryFailedCard,
            #SummaryPendingCard {
                border-radius: 12px;
            }
            #SummaryTotalCard   { background-color: #EFF6FF; }  /* light blue  */
            #SummaryPassedCard  { background-color: #ECFDF3; }  /* light green */
            #SummaryFailedCard  { background-color: #FEF2F2; }  /* light red   */
            #SummaryPendingCard { background-color: #FFFBEB; }  /* light yellow*/

            #SummaryNumber {
                font-size: 18pt;
                font-weight: 700;
            }
            #SummaryTotalCard #SummaryNumber   { color: #2563EB; }  /* blue   */
            #SummaryPassedCard #SummaryNumber  { color: #16A34A; }  /* green  */
            #SummaryFailedCard #SummaryNumber  { color: #DC2626; }  /* red    */
            #SummaryPendingCard #SummaryNumber { color: #D97706; }  /* amber  */

            #SummaryCaption {
                font-size: 10pt;
                color: #4B5563;
            }
            /* ===== Custom vertical scrollbar for QC form ===== */
            QScrollArea {
                border: none;
            }
            /* ===== Custom vertical scrollbar for QC form ===== */
            QScrollArea {
                border: none;
            }

            QScrollBar:vertical {
                background: #E5E7EB;      /* light grey track */
                width: 14px;
                margin: 0px;              /* 🔥 no gap at top/bottom */
                border-radius: 7px;
            }

            QScrollBar::handle:vertical {
                background: #3B82F6;      /* blue thumb */
                min-height: 48px;
                border-radius: 7px;
            }

            QScrollBar::handle:vertical:hover {
                background: #2563EB;      /* darker blue on hover */
            }

            /* Remove arrow-button area so track is full height */
            QScrollBar::sub-line:vertical,
            QScrollBar::add-line:vertical {
                height: 0px;
                margin: 0px;
                border: none;
                background: transparent;
            }

            /* Hide arrows completely */
            QScrollBar::up-arrow:vertical,
            QScrollBar::down-arrow:vertical {
                width: 0px;
                height: 0px;
                border: none;
                background: transparent;
            }

            /* Pages above/below thumb */
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {
                background: transparent;
            }
            QPushButton#OverallPassButton,
            QPushButton#OverallFailButton {
                padding: 16px 40px;        /* bigger padding */
                min-width: 160px;          /* wider button  */
                min-height: 60px;          /* taller button */
                font-size: 25pt;           /* bigger text   */
                border-radius: 12px;       /* smoother edges */
                font-weight: 700;
                background: #FFFFFF;
                border: 2px solid #CCCCCC;
            }

            /* PASS selected */
            QPushButton#OverallPassButton:checked {
                background: #C8F5C8;
                border: 2px solid #9EDB9E;
                color: #065F46;
            }

            /* FAIL selected */
            QPushButton#OverallFailButton:checked {
                background: #F6C8C8;
                border: 2px solid #E89A9A;
                color: #7F1D1D;
            }
            #TopHeader {
                background-color: #FFFFFF;
            }
            /* ================== DIGITAL SCALE (GLOBAL FIX) ================== */

            #ScaleDisplayFrame {
                background-color: #020617;      /* pure black */
                border-radius: 16px;
            }

            #ScaleWeightLabel {
                color: #22C55E;                 /* green text */
                font-size: 32pt;
                font-weight: 700;
            }

            #ScaleMinLabel, #ScaleMaxLabel {
                color: #FACC15;                 /* yellow */
                font-size: 14pt;
                font-weight: 600;
            }

            #ScaleDialog {
                background: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 #FFFFFF,
                    stop:1 #EBF2FF
                );
                border: 2px solid black;
                border-radius: 12px;
            }

            /* Make Metal / Label / Dirty / Plastic blue-background inside scale */
            #ScaleLabelGroup,
            #ScaleDirtyGroup,
            #ScaleMetalGroup,
            #ScalePlasticGroup {
                background-color: #CCE2E9;
                border-radius: 8px;
                border: 1px solid #94A3B8;
                padding: 10px;
            }

            #SignupLabel {
                color: #6B7280;
                font-size: 9pt;
                background-color: #FFFFFF;
                padding: 2px 6px;
                border-radius: 4px;

                /* 🔥 IMPORTANT */
                qproperty-alignment: AlignCenter;
            }

            #SignupLabel a {
                color: #2563EB;
                font-weight: 600;
                text-decoration: none;
            }
            #SignupLabel a:hover {
                text-decoration: underline;
            }
        """)

        
# ----------------- Digital Weight Dialog ----------------- #

class DigitalWeightDialog(QDialog):
    def __init__(
        self,
        parent=None,
        weight_edit=None,
        card=None,
        require_bag_selection=True,
        enforce_minmax_for_ok=False,
    ):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setWindowTitle("Digital Weight Scale")
        self.setModal(True)
        self.setObjectName("ScaleDialog")
        self.require_bag_selection = bool(require_bag_selection)
        # ✅ Reporting-only behavior: OK is enabled ONLY after Lock Weight AND within Min/Max
        self.enforce_minmax_for_ok = bool(enforce_minmax_for_ok)

        self.weight_edit = weight_edit
        self.card = card


        # ✅ ZERO/TARE control (Set Zero)
        self._zero_offset = 0.0          # raw kg to subtract
        self._last_raw_weight = None     # last raw reading from scale
        self._weight_locked = False      # freeze display when Lock Weight is ON
        # make a bit taller to fit extra controls
        self.setFixedSize(420, 620)

        main = QVBoxLayout(self)
        main.setContentsMargins(24, 24, 24, 24)
        main.setSpacing(16)

        # Header row: title + close "X"
        header = QHBoxLayout()
        # Determine Bag Number from card title "Bag #X"
        bag_number = "?"
        if card is not None:
            bag_title_lbl = card.findChild(QLabel, "BagTitle")
            if bag_title_lbl:
                bag_number = bag_title_lbl.text().replace("Bag #", "")

        title = QLabel(f"Digital Weight Scale  •  Bag #{bag_number}")
        title.setStyleSheet("font-size: 16pt; font-weight: 600;")
        self.title_label = title
        header.addWidget(title)

        header.addStretch()
        close_btn = QToolButton()
        close_btn.setText("✕")
        close_btn.clicked.connect(self.reject)
        header.addWidget(close_btn)
        main.addLayout(header)

        # -------- Black display frame (ONLY min/max + big weight) --------
        display_frame = QFrame()
        display_frame.setObjectName("ScaleDisplayFrame")
        display_layout = QVBoxLayout(display_frame)
        display_layout.setContentsMargins(24, 24, 24, 24)
        display_layout.setSpacing(10)

        # --- Min / Max Row (TOP) ---
        # --- Min / Max Row (TOP) ---
        minmax_row = QHBoxLayout()
        minmax_row.setSpacing(20)

        # default text when parent has no weight fields (e.g. BagDetailsDialog)
        min_text = "Min: -- kg"
        max_text = "Max: -- kg"

        if parent is not None and hasattr(parent, "min_weight_edit"):
            txt = parent.min_weight_edit.text().strip()
            if txt:
                if not txt.endswith("kg"):
                    txt = f"{txt} kg"
                min_text = f"Min: {txt}"

        if parent is not None and hasattr(parent, "max_weight_edit"):
            txt = parent.max_weight_edit.text().strip()
            if txt:
                if not txt.endswith("kg"):
                    txt = f"{txt} kg"
                max_text = f"Max: {txt}"

        self.min_label = QLabel(min_text)
        self.min_label.setObjectName("ScaleMinLabel")
        self.min_label.setStyleSheet(
            "color: #FACC15; font-size: 12pt; font-weight: 600;"
        )

        self.max_label = QLabel(max_text)
        self.max_label.setObjectName("ScaleMaxLabel")
        self.max_label.setStyleSheet(
            "color: #FACC15; font-size: 12pt; font-weight: 600;"
        )

        minmax_row.addWidget(self.min_label)
        minmax_row.addStretch()
        minmax_row.addWidget(self.max_label, 0, Qt.AlignRight)


        display_layout.addLayout(minmax_row)

        # --- ACTUAL WEIGHT (BIG NUMBER) ---
        self.weight_label = QLabel("0.00 kg")
        self.weight_label.setObjectName("ScaleWeightLabel")
        self.weight_label.setAlignment(Qt.AlignCenter)
        display_layout.addWidget(self.weight_label)

        # 🔁 Start auto-reading from Modbus scale
        self.read_timer = QTimer(self)
        self.read_timer.timeout.connect(self._read_from_modbus)
        self.read_timer.start(200)   # read every 300 ms

        main.addWidget(display_frame)


        # -------- Buttons row: OK (blue) + Reset (red) --------
        btn_row = QHBoxLayout()

        self.ok_btn = QPushButton("OK")
        self.ok_btn.setObjectName("ScaleOkButton")
        self.ok_btn.setMinimumHeight(48)
        self.ok_btn.clicked.connect(self._accept_weight)

        # ✅ NEW: Lock Weight (toggle)
        self.lock_btn = QPushButton("Lock Weight")
        self.lock_btn.setObjectName("ScaleLockButton")
        self.lock_btn.setMinimumHeight(48)
        self.lock_btn.setCheckable(True)
        self.lock_btn.clicked.connect(self._toggle_lock_weight)

        reset_btn = QPushButton("Reset to 0.00")
        reset_btn.setObjectName("ScaleResetButton")
        reset_btn.setMinimumHeight(48)
        reset_btn.clicked.connect(self._reset_weight)

        btn_row.addWidget(self.ok_btn)
        btn_row.addWidget(self.lock_btn)
        btn_row.addWidget(reset_btn)
        main.addLayout(btn_row)

        # ✅ Reporting-only: start disabled; enable only after Lock Weight AND within Min/Max
        if getattr(self, "enforce_minmax_for_ok", False):
            try:
                self.ok_btn.setEnabled(False)
            except Exception:
                pass

        # -------- NEW: Metal / Label / Dirty / Plastic BELOW buttons --------
        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(10)

        # Metal Detector *
        self.metal_ok = QRadioButton("OK")
        self.metal_no = QRadioButton("No")
        metal_group = QGroupBox("Metal Detector Test *")
        metal_group.setObjectName("ScaleMetalGroup")

        ml = QVBoxLayout(metal_group)
        ml.addWidget(self.metal_ok)
        ml.addWidget(self.metal_no)
        grid.addWidget(metal_group, 0, 0)

        # Label *
        self.label_ok = QRadioButton("OK")
        self.label_no = QRadioButton("No")
        label_group = QGroupBox("Label *")
        label_group.setObjectName("ScaleLabelGroup")
        ll = QVBoxLayout(label_group)
        ll.addWidget(self.label_ok)
        ll.addWidget(self.label_no)
        grid.addWidget(label_group, 0, 1)

        # Dirty *
        self.dirty_yes = QRadioButton("Yes")
        self.dirty_no = QRadioButton("No")
        dirty_group = QGroupBox("Dirty *")
        dirty_group.setObjectName("ScaleDirtyGroup")

        dl = QVBoxLayout(dirty_group)
        dl.addWidget(self.dirty_yes)
        dl.addWidget(self.dirty_no)
        grid.addWidget(dirty_group, 1, 0)

        # Plastic *
        self.plastic_ok = QRadioButton("OK")
        self.plastic_no = QRadioButton("No")
        plastic_group = QGroupBox("Plastic *")
        plastic_group.setObjectName("ScalePlasticGroup")

        pl = QVBoxLayout(plastic_group)
        pl.addWidget(self.plastic_ok)
        pl.addWidget(self.plastic_no)
        grid.addWidget(plastic_group, 1, 1)

        main.addLayout(grid)

        # ✅ Prefill scale radios from the bag card (so when you open scale again, it shows the saved selections)
        try:
            if self.card is not None:
                mg = self.card.findChild(QGroupBox, "MetalGroup")
                if mg:
                    ok = mg.layout().itemAt(0).widget()
                    no = mg.layout().itemAt(1).widget()
                    self.metal_ok.setChecked(ok.isChecked())
                    self.metal_no.setChecked(no.isChecked())

                lg = self.card.findChild(QGroupBox, "LabelGroup")
                if lg:
                    ok = lg.layout().itemAt(0).widget()
                    no = lg.layout().itemAt(1).widget()
                    self.label_ok.setChecked(ok.isChecked())
                    self.label_no.setChecked(no.isChecked())

                dg = self.card.findChild(QGroupBox, "DirtyGroup")
                if dg:
                    yes = dg.layout().itemAt(0).widget()
                    no = dg.layout().itemAt(1).widget()
                    self.dirty_yes.setChecked(yes.isChecked())
                    self.dirty_no.setChecked(no.isChecked())

                pg = self.card.findChild(QGroupBox, "PlasticGroup")
                if pg:
                    ok = pg.layout().itemAt(0).widget()
                    no = pg.layout().itemAt(1).widget()
                    self.plastic_ok.setChecked(ok.isChecked())
                    self.plastic_no.setChecked(no.isChecked())
        except Exception:
            pass


        # -------- NEW: Bag Number + Unloading Operator (below Dirty/Plastic) --------
        bagop_grid = QGridLayout()
        bagop_grid.setHorizontalSpacing(16)
        bagop_grid.setVerticalSpacing(6)

        # Unloading Operator *
        lbl_uop = QLabel("Unloading Operator *")
        lbl_uop.setStyleSheet("font-weight:600;")
        self.scale_unloading_operator_edit = QLineEdit()
        self.scale_unloading_operator_edit.setReadOnly(True)
        self.scale_unloading_operator_edit.setPlaceholderText("Auto from DB")
        self.scale_unloading_operator_edit.setObjectName("ScaleUnloadingOperatorEdit")

        # ✅ Prefill operator from card (if already chosen before)
        try:
            if self.card is not None and hasattr(self.card, "_unloading_operator"):
                t = str(getattr(self.card, "_unloading_operator") or "").strip()
                if t:
                    self.scale_unloading_operator_edit.setText(t)
        except Exception:
            pass


        # Bag Number
        lbl_bag = QLabel("Bag Number")
        lbl_bag.setStyleSheet("font-weight:600;")
        self.scale_bag_number_combo = QComboBox()
        self.scale_bag_number_combo.setObjectName("ScaleBagNumberCombo")
        self.scale_bag_number_combo.addItem("Select Bag")

        bagop_grid.addWidget(lbl_uop, 0, 0)
        bagop_grid.addWidget(lbl_bag, 0, 1)
        bagop_grid.addWidget(self.scale_unloading_operator_edit, 1, 0)
        bagop_grid.addWidget(self.scale_bag_number_combo, 1, 1)

        main.addLayout(bagop_grid)

        # --- load bag list from parent carton_no, using your existing DB functions ---
        def _sync_bag_and_operator():
            if parent is None:
                return
            if self.scale_bag_number_combo.currentIndex() <= 0:
                self.scale_unloading_operator_edit.clear()
                # also clear main page
                if hasattr(parent, "unloading_operator_edit"):
                    parent.unloading_operator_edit.clear()
                return

            carton_no = ""
            if hasattr(parent, "carton_no_edit"):
                carton_no = parent.carton_no_edit.text().strip()

            # ✅ If carton changed, reset used-bag tracking
            if getattr(parent, "_scale_last_carton_for_bags", None) != carton_no:
                parent._scale_last_carton_for_bags = carton_no
                parent._scale_used_bag_numbers = set()
                parent._scale_bag_selected_by_index = {}

            bag_no = self.scale_bag_number_combo.currentText().strip()
            op = ""
            if hasattr(parent, "_fetch_unloading_operator_for_carton_bag"):
                op = parent._fetch_unloading_operator_for_carton_bag(carton_no, bag_no)

            self.scale_unloading_operator_edit.setText(op)

            # ✅ store into this bag card (per-bag, not global)
            try:
                if self.card is not None:
                    self.card._bag_number_raw = bag_no or ""
                    self.card._unloading_operator = op or ""
                    total = ""
                    try:
                        total = str(getattr(parent, "total_bags", "") or "")
                    except Exception:
                        total = ""
                    disp = ""
                    if bag_no:
                        disp = bag_no if "/" in bag_no else (f"{bag_no}/{total}" if total else bag_no)
                    self.card._bag_number_display = disp
            except Exception:
                pass


            # ✅ keep your main page fields updated (so Save Inspection still works)
            if hasattr(parent, "unloading_operator_edit"):
                parent.unloading_operator_edit.setText(op)
            if hasattr(parent, "bag_number_combo"):
                # set same selected bag in main combo (if exists in list)
                idx = parent.bag_number_combo.findText(bag_no)
                if idx >= 0:
                    parent.bag_number_combo.setCurrentIndex(idx)

        self.scale_bag_number_combo.currentIndexChanged.connect(_sync_bag_and_operator)

        # Fill bag combo from DB (same carton_no logic you already use)
        try:
            carton_no = ""
            if parent is not None and hasattr(parent, "carton_no_edit"):
                carton_no = parent.carton_no_edit.text().strip()

            bags = []
            if parent is not None and hasattr(parent, "_fetch_bag_numbers_from_loop_dt_bag"):
                bags = parent._fetch_bag_numbers_from_loop_dt_bag(carton_no) or []


            if bags:
                # ✅ Filter out bag numbers already used by previous bags for this carton
                used = set(getattr(parent, "_scale_used_bag_numbers", set()) or set())
                current_idx = getattr(self.card, "_bag_index", None) if self.card is not None else None
                current_selected = ""
                if current_idx is not None:
                    current_selected = str(getattr(parent, "_scale_bag_selected_by_index", {}).get(current_idx, "")).strip()

                filtered = []
                for b in bags:
                    bs = str(b).strip()
                    if not bs:
                        continue
                    if bs in used and bs != current_selected:
                        continue
                    filtered.append(bs)

                self.scale_bag_number_combo.blockSignals(True)
                try:
                    self.scale_bag_number_combo.clear()
                    self.scale_bag_number_combo.addItem("Select Bag")
                    for bs in filtered:
                        self.scale_bag_number_combo.addItem(bs)

                    # preselect existing selection (if any) for this bag
                    if current_selected:
                        idx2 = self.scale_bag_number_combo.findText(current_selected)
                        if idx2 >= 0:
                            self.scale_bag_number_combo.setCurrentIndex(idx2)
                finally:
                    self.scale_bag_number_combo.blockSignals(False)
        except Exception:
            pass



        # Helper text
        helper = QLabel(
            "Place the bag on the scale and wait for stable reading\n"
            "Click OK to record the weight, or Reset to zero the scale"
        )
        helper.setWordWrap(True)
        main.addWidget(helper)

        # If text already in main Weight box, show it
        if self.weight_edit is not None and self.weight_edit.text().strip():
            txt = self.weight_edit.text().strip()
            if not txt.endswith("kg"):
                txt = f"{txt} kg"
            self.weight_label.setText(txt)

    def _toggle_lock_weight(self):
        # toggle state based on button checked
        self._weight_locked = self.lock_btn.isChecked()

        if self._weight_locked:
            # pause reading
            if hasattr(self, "read_timer") and self.read_timer.isActive():
                self.read_timer.stop()
            self.lock_btn.setText("Locked ✅")

            # ✅ Reporting-only: OK enabled only when locked AND within Min/Max
            if getattr(self, "enforce_minmax_for_ok", False):
                self._update_ok_button_by_limits(show_warning=True)
        else:
            # resume reading
            if hasattr(self, "read_timer") and not self.read_timer.isActive():
                self.read_timer.start(200)
            self.lock_btn.setText("Lock Weight")

            # ✅ Reporting-only: unlock => disable OK again
            if getattr(self, "enforce_minmax_for_ok", False):
                self._update_ok_button_by_limits(show_warning=False)


    def _parse_limit_value(self, text: str):
        """Extract float value from label text like 'Min: 1.76 kg'."""
        try:
            import re
            nums = re.findall(r"[-+]?\d+(?:\.\d+)?", str(text or ""))
            return float(nums[0]) if nums else None
        except Exception:
            return None


    def _get_current_weight_kg(self):
        """Return current displayed weight (kg) as float, or None."""
        try:
            import re
            txt = str(self.weight_label.text() or "")
            nums = re.findall(r"[-+]?\d+(?:\.\d+)?", txt)
            return float(nums[0]) if nums else None
        except Exception:
            return None


    def _is_within_minmax(self):
        """Return True if current weight is within [min,max]. If min/max missing, allow."""
        w = self._get_current_weight_kg()
        if w is None:
            return False

        mn = self._parse_limit_value(getattr(self, "min_label", None).text() if hasattr(self, "min_label") else "")
        mx = self._parse_limit_value(getattr(self, "max_label", None).text() if hasattr(self, "max_label") else "")

        # If min/max not available, don't block.
        if mn is None and mx is None:
            return True
        if mn is None:
            return w <= mx
        if mx is None:
            return w >= mn
        return (w >= mn) and (w <= mx)


    def _update_ok_button_by_limits(self, show_warning: bool = False):
        """Reporting-only behavior: enable OK only when locked and within limits."""
        if not getattr(self, "enforce_minmax_for_ok", False):
            return
        if not hasattr(self, "ok_btn"):
            return

        # must be locked
        if not getattr(self, "_weight_locked", False):
            try:
                self.ok_btn.setEnabled(False)
            except Exception:
                pass
            return

        ok = self._is_within_minmax()
        try:
            self.ok_btn.setEnabled(bool(ok))
        except Exception:
            pass

        if (not ok) and show_warning:
            try:
                QMessageBox.warning(
                    self,
                    "Out of Range",
                    "Locked weight is outside Min/Max.\n"
                    "Please adjust the bag weight to be within Min/Max before clicking OK.",
                )
            except Exception:
                pass


    def _reset_weight(self):
        """Set Zero (tare) so current reading becomes 0.00 kg."""
        # if locked, we still allow tare using last raw value
        raw = getattr(self, "_last_raw_weight", None)
        if raw is None:
            # try to parse from label as fallback
            try:
                raw = float((self.weight_label.text() or "0").replace("kg", "").strip())
            except Exception:
                raw = 0.0

        try:
            self._zero_offset = float(raw or 0.0)
        except Exception:
            self._zero_offset = 0.0

        # show zero immediately
        self.weight_label.setText("0.00 kg")


    def _read_from_modbus(self):
        """Live auto-read from COM3 and update the display."""

        # 🔒 If weight is locked → do nothing (freeze value)
        if getattr(self, "_weight_locked", False):
            return

        w = read_scale_weight('COM3')  # uses your global scale reader

        if w is not None:
            # keep last raw value (before tare)
            try:
                self._last_raw_weight = float(w)
            except Exception:
                self._last_raw_weight = w

            # apply tare/zero offset
            try:
                adj = float(w) - float(getattr(self, "_zero_offset", 0.0) or 0.0)
            except Exception:
                adj = w

            # clamp tiny negatives caused by noise
            try:
                if adj < 0 and abs(adj) < 0.05:
                    adj = 0.0
            except Exception:
                pass

            # always show 2 decimal places
            try:
                self.weight_label.setText(f"{float(adj):.2f} kg")
            except Exception:
                self.weight_label.setText(f"{adj} kg")

        # ❌ DO NOTHING if w is None
        # → last valid weight stays on screen (NO flicker, NO reset)

    def _accept_weight(self):
        # ✅ REQUIRE Bag Number selection before OK (only when enabled)
        if getattr(self, "require_bag_selection", True):
            try:
                if hasattr(self, "scale_bag_number_combo"):
                    sel = self.scale_bag_number_combo.currentText().strip()
                    if (not sel) or sel.lower().startswith("select"):
                        QMessageBox.warning(
                            self,
                            "Bag Number Required",
                            "Please select Bag Number before clicking OK.",
                        )
                        return
            except Exception:
                pass

        # ✅ REQUIRE Lock Weight before OK
        if not getattr(self, "_weight_locked", False):
            QMessageBox.warning(
                self,
                "Lock Weight Required",
                "Please click 'Lock Weight' first before clicking OK."
            )
            return

        # ✅ REQUIRE Metal / Label / Dirty / Plastic selection before OK
        try:
            missing = []
            if not (self.metal_ok.isChecked() or self.metal_no.isChecked()):
                missing.append("Metal Detector Test")
            if not (self.label_ok.isChecked() or self.label_no.isChecked()):
                missing.append("Label")
            if not (self.dirty_yes.isChecked() or self.dirty_no.isChecked()):
                missing.append("Dirty")
            if not (self.plastic_ok.isChecked() or self.plastic_no.isChecked()):
                missing.append("Plastic")

            if missing:
                QMessageBox.warning(
                    self,
                    "Selection Required",
                    "Please select: " + ", ".join(missing) + " before clicking OK.",
                )
                return
        except Exception:
            # If any widget missing, do not block flow
            pass

        # copy value back to main Weight (without " kg")
        if self.weight_edit is not None:
            txt = self.weight_label.text().replace("kg", "").strip()
            self.weight_edit.setText(txt)

        if self.card is not None:
            # --- update Metal Detector on card ---
            metal_group = self.card.findChild(QGroupBox, "MetalGroup")
            if metal_group:
                metal_ok = metal_group.layout().itemAt(0).widget()
                metal_no = metal_group.layout().itemAt(1).widget()
                metal_ok.setChecked(self.metal_ok.isChecked())
                metal_no.setChecked(self.metal_no.isChecked())

            # --- update Label on card ---
            label_group = self.card.findChild(QGroupBox, "LabelGroup")
            if label_group:
                label_ok = label_group.layout().itemAt(0).widget()
                label_no = label_group.layout().itemAt(1).widget()
                label_ok.setChecked(self.label_ok.isChecked())
                label_no.setChecked(self.label_no.isChecked())

            # --- update Dirty on card ---
            dirty_group = self.card.findChild(QGroupBox, "DirtyGroup")
            if dirty_group:
                dirty_yes = dirty_group.layout().itemAt(0).widget()
                dirty_no = dirty_group.layout().itemAt(1).widget()
                dirty_yes.setChecked(self.dirty_yes.isChecked())
                dirty_no.setChecked(self.dirty_no.isChecked())

            # --- update Plastic on card ---
            plastic_group = self.card.findChild(QGroupBox, "PlasticGroup")
            if plastic_group:
                plastic_ok = plastic_group.layout().itemAt(0).widget()
                plastic_no = plastic_group.layout().itemAt(1).widget()
                plastic_ok.setChecked(self.plastic_ok.isChecked())
                plastic_no.setChecked(self.plastic_no.isChecked())

            # -------- AUTO PASS / FAIL LOGIC (combo + min/max weight) --------
            status_pass = self.card.findChild(QRadioButton, "StatusPassRadio")
            status_fail = self.card.findChild(QRadioButton, "StatusFailRadio")

            # Good combo means:
            # Metal = OK, Label = OK, Plastic = OK, Dirty = No
            good_combo = (
                self.metal_ok.isChecked()
                and self.label_ok.isChecked()
                and self.plastic_ok.isChecked()
                and self.dirty_no.isChecked()
            )

            # ✅ NEW: weight must be within min/max
            weight_val = None
            try:
                # use the same txt we copied into main weight_edit
                weight_val = float((txt or "").strip())
            except Exception:
                weight_val = None

            min_val = max_val = None
            try:
                p = self.parent()
                if p is not None and hasattr(p, "min_weight_edit") and hasattr(p, "max_weight_edit"):
                    min_val = float((p.min_weight_edit.text() or "").strip())
                    max_val = float((p.max_weight_edit.text() or "").strip())
            except Exception:
                min_val = max_val = None

            in_range = True
            if (weight_val is not None) and (min_val is not None) and (max_val is not None):
                in_range = (min_val <= weight_val <= max_val)

            # ✅ PASS only if combo OK AND weight in range
            if good_combo and in_range:
                if status_fail is not None:
                    status_fail.setChecked(False)
                if status_pass is not None:
                    status_pass.setChecked(True)
            else:
                if status_pass is not None:
                    status_pass.setChecked(False)
                if status_fail is not None:
                    status_fail.setChecked(True)

            # ✅ NEW: after OK → show Unloading Operator + Bag Number on Inspection Progress card
            try:
                bag_no = ""
                if hasattr(self, "scale_bag_number_combo"):
                    bag_no = self.scale_bag_number_combo.currentText().strip()
                    if bag_no.lower().startswith("select"):
                        bag_no = ""

                op = ""
                if hasattr(self, "scale_unloading_operator_edit"):
                    op = self.scale_unloading_operator_edit.text().strip()

                if self.card is not None:
                    if hasattr(self.card, "bag_op_meta"):
                        self.card.bag_op_meta.setText(op or "--")

                    if hasattr(self.card, "bag_no_meta"):
                        total = ""
                        try:
                            total = str(getattr(self.parent(), "total_bags", "") or "")
                        except Exception:
                            total = ""

                        if bag_no:
                            if "/" in bag_no:
                                self.card.bag_no_meta.setText(bag_no)
                            else:
                                if total:
                                    self.card.bag_no_meta.setText(f"{bag_no}/{total}")
                                else:
                                    self.card.bag_no_meta.setText(bag_no)
                        else:
                            self.card.bag_no_meta.setText("")

                # ✅ store per-card values for DB save
                try:
                    if self.card is not None:
                        self.card._bag_number_raw = bag_no or ""
                        self.card._unloading_operator = op or ""

                        disp = ""
                        if bag_no:
                            if "/" in bag_no:
                                disp = bag_no
                            else:
                                disp = f"{bag_no}/{total}" if total else bag_no
                        self.card._bag_number_display = disp
                except Exception:
                    pass
            except Exception:
                pass

        # ✅ Remember selected Bag Number so next bag won't show the same option
        try:
            parent = self.parent()
            if parent is not None and hasattr(self, "scale_bag_number_combo"):
                sel = self.scale_bag_number_combo.currentText().strip()
                if sel and not sel.lower().startswith("select"):
                    bag_idx = getattr(self.card, "_bag_index", None) if self.card is not None else None
                    if bag_idx is not None:
                        if not hasattr(parent, "_scale_bag_selected_by_index"):
                            parent._scale_bag_selected_by_index = {}
                        parent._scale_bag_selected_by_index[int(bag_idx)] = sel

                        used = set(v for v in parent._scale_bag_selected_by_index.values() if str(v).strip())
                        parent._scale_used_bag_numbers = used
        except Exception:
            pass

        self.accept()



class BagDetailsDialog(QDialog):
    def __init__(self, parent=None, carton_no=None, qc_no=None):
        super().__init__(parent)
        
        self.setWindowTitle(f"Bag Details - Carton: {carton_no}")
        self.resize(1600, 800)
        self.carton_no = carton_no
        self.qc_no = qc_no

        # store min/max from header so we can show in scale dialog
        self.header_min_weight = None
        self.header_max_weight = None

        self._apply_styles()
    
        self._setup_ui()
        self._load_data()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # 🔹 OPTION A TITLE ROW – shows "Bag Details - Carton: CTN-XXXXX"
        title_row = QHBoxLayout()
        title_label = QLabel(f"Bag Details - CTN NO: {self.carton_no or '-'}")
        title_label.setObjectName("SectionTitle")  # uses same bold style
        title_row.addWidget(title_label)
        title_row.addStretch()
        main_layout.addLayout(title_row)
        # 🔹 END OPTION A

        # Header Frame (light blue background, like image)
        header_frame = QFrame()
        header_frame.setObjectName("HeaderFrame")
        header_layout = QGridLayout(header_frame)
        header_layout.setContentsMargins(12, 8, 12, 8)
        header_layout.setSpacing(8)

                
        # Header card labels (top)
        self.qc_number_label = QLabel("—")
        self.checked_by_label = QLabel("—")
        self.total_bags_label = QLabel("—")
        self.packaging_operator_label = QLabel("—")   # ✅ NEW (was operator name)
        self.box_remarks_label = QLabel("—")

        header_layout.addWidget(QLabel("QC Number"), 0, 0)
        header_layout.addWidget(self.qc_number_label, 1, 0)

        header_layout.addWidget(QLabel("Checked By"), 0, 1)
        header_layout.addWidget(self.checked_by_label, 1, 1)

        header_layout.addWidget(QLabel("Total Bags"), 0, 2)
        header_layout.addWidget(self.total_bags_label, 1, 2)

        # ✅ NEW column: Packaging Operator
        header_layout.addWidget(QLabel("Packaging Operator"), 0, 3)
        header_layout.addWidget(self.packaging_operator_label, 1, 3)

        # move Box Remarks to last column
        header_layout.addWidget(QLabel("Box Remarks"), 0, 4)
        header_layout.addWidget(self.box_remarks_label, 1, 4)
        


        main_layout.addWidget(header_frame)

        # Individual Bag Inspection
        inspection_title = QLabel("Individual Bag Inspection")
        inspection_title.setObjectName("SectionTitle")
        main_layout.addWidget(inspection_title)

 

        self.bag_table = QTableWidget()
        self.bag_table.setObjectName("BagDetailsTable")
        self.bag_table.setColumnCount(9)
        self.bag_table.setHorizontalHeaderLabels([
            "NO.",
            "BAG NUMBER",
            "UNLOADING OPERATOR",
            "LOOP SIZE",
            "WEIGHT (KG)",
            "RECOVERY WEIGHT (KG)",          # ✅ NEW
            "STATUS",
            "REMARKS RECOVERED",
            "ACTIONS"
        ])
        self.bag_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.bag_table.verticalHeader().setVisible(False)
        self.bag_table.verticalHeader().setDefaultSectionSize(52)  # ✅ fix half-cut rows
        self.bag_table.setWordWrap(False)
        self.bag_table.setTextElideMode(Qt.ElideRight)
        self.bag_table.verticalHeader().hide()
        self.bag_table.verticalHeader().setDefaultSectionSize(52)
        self.bag_table.setEditTriggers(QTableWidget.NoEditTriggers)
        main_layout.addWidget(self.bag_table)

        # Bottom buttons  →  Save All Changes  +  Close (right side)
        bottom_layout = QHBoxLayout()
        bottom_layout.addStretch()

        save_btn = QPushButton("Save All Changes")
        save_btn.setObjectName("SaveButton")
        save_btn.clicked.connect(self._save_changes)
        bottom_layout.addWidget(save_btn)

        close_btn = QPushButton("Close")
        close_btn.setObjectName("CloseButton")
        close_btn.clicked.connect(self.close)
        bottom_layout.addWidget(close_btn)

        main_layout.addLayout(bottom_layout)



    def _load_data(self):
        """Fetch header + bag rows from MySQL and fill the dialog."""
        if not DB_ENABLED:
            return

        try:
            conn = mysql.connector.connect(
                host=DB_HOST,
                port=DB_PORT,
                user=DB_USER,
                password=DB_PASSWORD,
                database=DB_NAME,
                use_pure=True,
            )
            cur = conn.cursor()

            # --- 1) Header info from gama_qc_carton_header -----------------
            cur.execute(
                f"""
                SELECT qc_no,
                    check_by,
                    total_bags,
                    box_remarks,
                    operator_name,
                    loop_size,
                    min_weight,
                    max_weight
                FROM {DB_QC_HEADER}
                WHERE carton_no = %s
                ORDER BY id DESC
                LIMIT 1
                """,
                (self.carton_no,),
            )
            header = cur.fetchone()

            operator_name_for_bags = ""
            loop_size_for_bags = ""

            if header:
                qc_no, check_by, total_bags, box_remarks, op_name, loop_size, min_w, max_w = header

                self.qc_number_label.setText(qc_no or "N/A")
                self.checked_by_label.setText(check_by or "N/A")
                self.total_bags_label.setText(str(total_bags) if total_bags is not None else "N/A")
                self.box_remarks_label.setText(box_remarks or "–")
                self.packaging_operator_label.setText(op_name or "—")

                operator_name_for_bags = op_name or ""
                # ✅ NEW: show operator name in header
                if hasattr(self, "operator_name_label"):
                    self.packaging_operator_label.setText(operator_name_for_bags or "–")

                loop_size_for_bags = loop_size or ""

                # ✅ store min/max for the scale dialog
                self.header_min_weight = min_w
                self.header_max_weight = max_w
            else:
                # No header found – show something sensible
                self.qc_number_label.setText("N/A")
                self.checked_by_label.setText("N/A")
                self.total_bags_label.setText("0")
                self.box_remarks_label.setText("No record found")
                if hasattr(self, "operator_name_label"):
                    self.packaging_operator_label.setText("–")


            # --- 2) Bag rows from gama_qc_carton_bag -----------------------
            cur.execute(
                f"""
                SELECT bag_no,
                    bag_number,
                    unloading_operator,
                    weight_kg,
                    recovery_weight_kg,    -- ✅ NEW
                    final_status,
                    remarks,
                    recovered_remarks,
                    actions
                FROM gama_qc_carton_bag
                WHERE qc_no    = %s
                AND carton_no = %s
                ORDER BY bag_no

                """,
                (self.qc_no, self.carton_no),
            )
            bags = cur.fetchall()

            self.bag_table.setRowCount(0)

            for row_idx, (bag_no, bag_number, unloading_operator, weight_kg,  recovery_weight_kg, final_status, remarks,
                        recovered_remarks, actions) in enumerate(bags):

                self.bag_table.insertRow(row_idx)
                self.bag_table.setRowHeight(row_idx, 52)

                # Helper to make a read-only item
                def make_ro_item(text: str) -> QTableWidgetItem:
                    item = QTableWidgetItem(text)
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    return item

                # BAG NO. (col 0)
                self.bag_table.setItem(row_idx, 0, make_ro_item(str(bag_no)))

                # BAG NUMBER (col 1) ✅ NEW
                self.bag_table.setItem(row_idx, 1, make_ro_item(str(bag_number or "")))

                # UNLOADING OPERATOR (col 2) ✅ NEW
                self.bag_table.setItem(row_idx, 2, make_ro_item(str(unloading_operator or "")))

                # LOOP SIZE (col 3)
                loop_text = f"{loop_size_for_bags}" if loop_size_for_bags else ""
                self.bag_table.setItem(row_idx, 3, make_ro_item(loop_text))

                self.bag_table.setItem(row_idx, 4, make_ro_item("" if weight_kg is None else f"{float(weight_kg):.2f}"))

                # ✅ RECOVERY WEIGHT (KG) (col 5) — show ONLY when already recovered
                act_low = (actions or "").strip().lower()

                rw_text = ""
                if act_low == "recover" and recovery_weight_kg is not None:
                    rw_text = f"{float(recovery_weight_kg):.2f}"

                self.bag_table.setItem(row_idx, 5, make_ro_item(rw_text))


                # STATUS pill (col 4)
                status_text = (final_status or "Pending").upper()

                # --- STATUS pill (centered) ---
                status_lbl = QLabel(
                    "Passed" if status_text in ("PASSED", "PASS") else
                    "Failed" if status_text in ("FAILED", "FAIL") else
                    "Pending"
                )
                if status_text in ("PASSED", "PASS"):
                    status_lbl.setObjectName("HistPassPill")
                elif status_text in ("FAILED", "FAIL"):
                    status_lbl.setObjectName("HistFailPill")
                else:
                    status_lbl.setObjectName("HistPendingPill")

                status_lbl.setAlignment(Qt.AlignCenter)
                status_lbl.setAttribute(Qt.WA_StyledBackground, True)
                status_lbl.setMinimumHeight(28)
                status_lbl.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

                status_lbl.setFixedHeight(28)
                status_lbl.setMinimumWidth(90)
                status_wrap = QWidget()
                status_wrap.setAttribute(Qt.WA_StyledBackground, True)
                status_lay = QHBoxLayout(status_wrap)
                status_lay.setContentsMargins(0, 0, 0, 0)
                status_lay.addStretch()
                status_lay.addWidget(status_lbl)
                status_lay.addStretch()

                self.bag_table.setCellWidget(row_idx, 6, status_wrap)

                self.bag_table.setRowHeight(row_idx, 60)  # ✅ enough height for pills/inputs/buttons


                # ---------- REMARKS RECOVERED (col 5) ----------
                status_upper = (status_text or "").upper()
                if status_upper in ("FAILED", "FAIL"):
                    rec_edit = QLineEdit()
                    rec_edit.setObjectName("BagRecoveredEdit")
                    rec_edit.setPlaceholderText("Remarks after recovery")
                    if recovered_remarks:
                        rec_edit.setText(recovered_remarks)

                    # make it look like an input box (no weird underline)
                    rec_edit.setMinimumHeight(32)
                    rec_edit.setStyleSheet(
                        "QLineEdit#BagRecoveredEdit {"
                        "background:#FFFFFF;"
                        "border:1px solid #D1D5DB;"
                        "border-radius:8px;"
                        "padding:6px 10px;"
                        "}"
                        "QLineEdit#BagRecoveredEdit:focus {"
                        "border:2px solid #2563EB;"
                        "}"
                    )

                    rec_wrap = QWidget()
                    rec_wrap.setAttribute(Qt.WA_StyledBackground, True)
                    rec_lay = QHBoxLayout(rec_wrap)
                    rec_lay.setContentsMargins(6, 2, 6, 2)
                    rec_lay.addWidget(rec_edit)
                    self.bag_table.setCellWidget(row_idx, 7, rec_wrap)
                else:
                    # Passed / Pending → show text but read-only
                    self.bag_table.setItem(
                        row_idx, 7, make_ro_item(recovered_remarks or "")
                    )

                # ---------- ACTIONS (col 6) ----------
                if status_upper in ("FAILED", "FAIL"):
                    existing_action = (actions or "").strip()
                    btn_text = "Recover" if existing_action.lower() == "recover" else "✏ Edit"

                    act_btn = QPushButton(btn_text)
                    act_btn.setCursor(Qt.PointingHandCursor)
                    act_btn.setMinimumHeight(32)

                    if btn_text == "Recover":
                        act_btn.setStyleSheet(
                            "QPushButton {"
                            "background-color:#16A34A;"
                            "color:#FFFFFF;"
                            "border:none;"
                            "border-radius:8px;"
                            "padding:6px 14px;"
                            "font-weight:700;"
                            "}"
                            "QPushButton:hover { background-color:#15803D; }"
                        )
                    else:
                        act_btn.setStyleSheet(
                            "QPushButton {"
                            "background-color:#FFFFFF;"
                            "color:#111827;"
                            "border:1px solid #D1D5DB;"
                            "border-radius:8px;"
                            "padding:6px 14px;"
                            "font-weight:700;"
                            "}"
                            "QPushButton:hover { background-color:#F3F4F6; }"
                        )

                    act_btn.clicked.connect(lambda _, r=row_idx: self._on_edit_clicked(r))

                    act_wrap = QWidget()
                    act_wrap.setAttribute(Qt.WA_StyledBackground, True)
                    act_lay = QHBoxLayout(act_wrap)
                    act_lay.setContentsMargins(0, 0, 0, 0)
                    act_lay.addStretch()
                    act_lay.addWidget(act_btn)
                    act_lay.addStretch()

                    self.bag_table.setCellWidget(row_idx, 8, act_wrap)
                else:
                    # Non-failed rows → just show text (usually empty)
                    self.bag_table.setItem(
                        row_idx, 8, make_ro_item(actions or "")
                    )

            cur.close()
            conn.close()

        except Exception as e:
            QMessageBox.critical(
                self,
                "Database error",
                f"Database error: {e}",
            )

    def _save_changes(self):
        """
        Save:
        - RECOVERY WEIGHT (KG) column  →  recovery_weight_kg
        - REMARKS RECOVERED column     →  recovered_remarks
        - ACTIONS column              →  actions
        into table gama_qc_carton_bag.

        Key used: (qc_no, carton_no, bag_no)
        """
        if not DB_ENABLED:
            QMessageBox.information(
                self,
                "Database disabled",
                "DB_ENABLED = False, nothing was written to MySQL.",
            )
            return

        if not self.qc_no or not self.carton_no:
            QMessageBox.warning(
                self,
                "Missing key",
                "QC Number or Carton Number is missing.\n"
                "Cannot update bag records.",
            )
            return

        try:
            conn = mysql.connector.connect(
                host=DB_HOST,
                port=DB_PORT,
                user=DB_USER,
                password=DB_PASSWORD,
                database=DB_NAME,
                use_pure=True,
            )
            cur = conn.cursor()

            # ✅ UPDATED: include recovery_weight_kg
            sql = f"""
                UPDATE {DB_QC_BAG}
                SET recovery_weight_kg = %s,
                    recovered_remarks   = %s,
                    actions             = %s
                WHERE qc_no    = %s
                AND carton_no = %s
                AND bag_no    = %s
            """

            from PySide6.QtWidgets import QLineEdit, QPushButton, QLabel, QWidget

            for row in range(self.bag_table.rowCount()):
                # --- bag_no (col 0) ---
                bag_item = self.bag_table.item(row, 0)
                if not bag_item:
                    continue
                try:
                    bag_no = int(bag_item.text())
                except ValueError:
                    continue

                # =========================================================
                # ✅ NEW: RECOVERY WEIGHT (KG)  (col 5)
                # =========================================================
                recovery_weight_kg = None
                w_rw = self.bag_table.cellWidget(row, 5)
                if isinstance(w_rw, QLineEdit):
                    t = w_rw.text().strip()
                    if t:
                        try:
                            recovery_weight_kg = float(t)
                        except ValueError:
                            recovery_weight_kg = None
                elif isinstance(w_rw, QWidget):
                    rw_edit = w_rw.findChild(QLineEdit)
                    if rw_edit is not None:
                        t = rw_edit.text().strip()
                        if t:
                            try:
                                recovery_weight_kg = float(t)
                            except ValueError:
                                recovery_weight_kg = None
                else:
                    item_rw = self.bag_table.item(row, 5)
                    if item_rw:
                        t = item_rw.text().strip()
                        if t:
                            try:
                                recovery_weight_kg = float(t)
                            except ValueError:
                                recovery_weight_kg = None

                # =========================================================
                # ✅ SHIFTED: REMARKS RECOVERED now (col 7)
                # =========================================================
                rec_text = ""
                w_rec = self.bag_table.cellWidget(row, 7)
                if isinstance(w_rec, QLineEdit):
                    rec_text = w_rec.text().strip()
                elif isinstance(w_rec, QWidget):
                    rec_edit = w_rec.findChild(QLineEdit)
                    rec_text = rec_edit.text().strip() if rec_edit else ""
                else:
                    item_rec = self.bag_table.item(row, 7)
                    rec_text = item_rec.text().strip() if item_rec else ""

                # =========================================================
                # ✅ SHIFTED: ACTIONS now (col 8)
                # =========================================================
                act_text = ""
                w_act = self.bag_table.cellWidget(row, 8)
                if isinstance(w_act, (QPushButton, QLabel)):
                    act_text = w_act.text().strip()
                elif isinstance(w_act, QWidget):
                    act_btn = w_act.findChild(QPushButton)
                    act_lbl = w_act.findChild(QLabel)
                    if act_btn is not None:
                        act_text = act_btn.text().strip()
                    elif act_lbl is not None:
                        act_text = act_lbl.text().strip()
                else:
                    item_act = self.bag_table.item(row, 8)
                    act_text = item_act.text().strip() if item_act else ""

                rec_val = rec_text or None

                # Normalize action stored in DB so your existing COUNT logic keeps working
                act_norm = (act_text or "").strip()
                act_low = act_norm.lower()

                # ✅ What we want in MySQL:
                #   - If user really recovered → store 'recover'
                #   - If still "Edit" → store 'pending'
                if act_low in ("recover", "recovered") or "recover" in act_low:
                    act_val = "recover"
                elif "edit" in act_low:
                    act_val = "pending"
                elif "pending" in act_low:
                    act_val = "pending"
                else:
                    act_val = act_norm or None

                # Only keep recovered_remarks & recovery_weight_kg when it is actually recovered
                if act_val != "recover":
                    rec_val = None
                    recovery_weight_kg = None

                cur.execute(
                    sql,
                    (recovery_weight_kg, rec_val, act_val, self.qc_no, self.carton_no, bag_no),
                )

            conn.commit()
            cur.close()
            conn.close()

            QMessageBox.information(
                self,
                "Saved",
                "Recovery weight, remarks recovered and actions have been updated in MySQL.",
            )

            # 🔹 close dialog after saving
            self.accept()

        except Exception as e:
            QMessageBox.critical(
                self,
                "Database error",
                f"Failed to update bag records:\n{e}",
            )


    def _mark_all_passed(self):
        # Update table UI (and optionally DB)
        for row in range(self.bag_table.rowCount()):
            status_label = self.bag_table.cellWidget(row, 3)
            status_label.setText("Passed")
            status_label.setObjectName("PassedPill")
            status_label.setStyleSheet("")  # Refresh style
        self.bag_table.viewport().update()
        # TODO: Update DB with "passed" for all bags in this carton

    def _edit_bag(self, row):
        # TODO: Make row editable or open sub-dialog for edit, then save to DB
        bag_no = self.bag_table.item(row, 0).text()
        QMessageBox.information(self, "Edit", f"Editing Bag #{bag_no} (implement edit and DB UPDATE here).")

    def _apply_styles(self):
        self.setStyleSheet("""
            /* ================== DIALOG BACKGROUND ================== */
            QDialog {
                background-color: #FFFFFF;
                border: 1px solid #E5E7EB;
                border-radius: 8px;
            }
            QDialog, QWidget {
                background-color: #FFFFFF;
            }
            #HeaderFrame {
                background-color: #F0F9FF;   /* light blue header only */
                border: 1px solid #E5E7EB;
                border-radius: 8px;
            }
            /* ================== HEADER PANEL ================== */
            #HeaderFrame {
                background-color: #EFF6FF;     /* Light blue header */
                border-radius: 8px;
                border: 1px solid #E5E7EB;
            }
            #HeaderFrame QLabel {
                background-color: #EFF6FF;     /* values row + titles = blue */
            }
            /* ================== HEADER PANEL ================== */
            #HeaderFrame {
                background-color: #EFF6FF;     /* Light blue header */
                border-radius: 8px;
                border: 1px solid #E5E7EB;
            }
            QLabel#HeaderTitle {
                font-size: 16pt;
                font-weight: bold;
                color: #111827;
            }

            /* ================== LABELS ================== */
            QLabel {
                font-size: 11pt;
                color: #111827;
            }
            #SectionTitle {
                font-size: 14pt;
                font-weight: bold;
                margin-top: 8px;
                margin-bottom: 4px;
            }

            /* ================== TABLE ================== */
            QTableWidget {
                border: 1px solid #E5E7EB;
                border-radius: 8px;
                gridline-color: #E5E7EB;
                background-color: #FFFFFF;     /* White rows */
                alternate-background-color: #FFFFFF;
            }
            QHeaderView::section {
                background-color: #F9FAFB;
                padding: 8px;
                font-weight: bold;
                border: 1px solid #E5E7EB;
            }
            /* Force all table items pure white */
            QTableWidget::item {
                background-color: #FFFFFF;
                selection-background-color: #FFFFFF;
                color: #111827;
            }


            /* ================== STATUS PILLS ================== */
            QLabel#HistPassPill {
                background-color: #DCFCE7;
                color: #166534;
                padding: 4px 14px;
                border-radius: 999px;
                font-weight: 700;
                min-width: 86px;
            }
            QLabel#HistFailPill {
                background-color: #FEE2E2;
                color: #991B1B;
                padding: 4px 14px;
                border-radius: 999px;
                font-weight: 700;
                min-width: 86px;
            }
                           
            QLabel#HistPendingPill {
                background-color: #FEF3C7;
                color: #92400E;
                padding: 4px 14px;
                border-radius: 999px;
                font-weight: 700;
                min-width: 86px;
            }

            /* Recovered pill (after edit + reweigh) */
            #RecoveredPill {
                background-color: #DCFCE7;
                color: #166534;
                padding: 4px 14px;
                border-radius: 999px;
                font-weight: 700;
                min-width: 86px;
            }

            /* Disabled recovered action button */
            #RecoverButton {
                background-color: #16A34A;
                color: #FFFFFF;
                border-radius: 8px;
                padding: 6px 14px;
                font-weight: 700;
                border: none;
            }
            #RecoverButton:disabled {
                background-color: #16A34A;
                color: #FFFFFF;
                opacity: 0.85;
            }

            QLabel#HistNeutralPill {
                background-color: #E5E7EB;
                color: #111827;
                padding: 4px 14px;
                border-radius: 999px;
                font-weight: 700;
                min-width: 86px;
            }
            /* ================== STATUS PILLS ================== */
            #PassedPill {
                background-color: #D1FAE5;
                color: #065F46;
                padding: 4px 14px;
                border-radius: 20px;
                font-size: 11pt;
                font-weight: 600;
            }
            #FailedPill {
                background-color: #FEE2E2;
                color: #B91C1C;
                padding: 4px 14px;
                border-radius: 20px;
                font-size: 11pt;
                font-weight: 600;
            }

            /* ================== BUTTONS ================== */
            #SaveButton {
                background-color: #2563EB;
                color: #FFFFFF;
                border-radius: 6px;
                padding: 8px 18px;
                font-weight: bold;
            }
            #SaveButton:hover {
                background-color: #1D4ED8;
            }

            #MarkAllPassedButton {
                background-color: #22C55E;
                color: #FFFFFF;
                border-radius: 6px;
                padding: 10px 22px;
                font-weight: bold;
            }
            #MarkAllPassedButton:hover {
                background-color: #16A34A;
            }

            #CloseButton {
                background-color: #FFFFFF;
                border: 1px solid #E5E7EB;
                color: #111827;
                border-radius: 6px;
                padding: 10px 22px;
            }
            #CloseButton:hover {
                background-color: #F3F4F6;
            }

            /* ================== EDIT BUTTON (BLUE LINK) ================== */
            #EditButton {
                background-color: transparent;
                border: none;
                color: #2563EB;
                font-size: 12pt;        /* Nice size */
                font-weight: 600;
                padding: 0;
            }
            #EditButton:hover {
                text-decoration: underline;
            }
            /* ================== DIGITAL WEIGHT SCALE STYLES ================== */
            #ScaleDisplayFrame {
                background-color: #F9FAFB;
                border-radius: 8px;
                border: 1px solid #E5E7EB;
                padding: 16px;
            }
            #ScaleWeightLabel {
                font-size: 48pt;
                font-weight: 700;
                color: #2563EB;
            }
            #ScaleOkButton {
                background-color: #2563EB;
                color: #FFFFFF;
                border-radius: 6px;
                font-weight: 600;
            }
            #ScaleOkButton:hover {
                background-color: #1D4ED8;
            }
            #ScaleResetButton {
                background-color: #EF4444;
                color: #FFFFFF;
                border-radius: 6px;
                font-weight: 600;
            }
            #ScaleResetButton:hover {
                background-color: #DC2626;
            }
            #MetalGroup, #LabelGroup, #DirtyGroup, #PlasticGroup {
                border: 1px solid #E5E7EB;
                border-radius: 6px;
                background-color: #FFFFFF;
                background-color: transparent;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 3px;
                color: #374151;
                font-weight: 600;
            }
            QRadioButton {
                spacing: 8px;
                font-size: 12pt;
                color: #1f2937;
            }
            QRadioButton::indicator {
                width: 18px;
                height: 18px;
                border-radius: 9px;
                border: 1px solid #9ca3af;
                background: #FFFFFF;
            }
            QRadioButton::indicator:checked {
                background-color: #111827;
                border: 1px solid #111827;
            }
            /* ===== DIGITAL WEIGHT SCALE (same as Start Inspection) ===== */
            #ScaleDialog {
                background: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 #FFFFFF,      /* top white */
                    stop:1 #EBF2FF       /* bottom light blue */
                );
                border: 2px solid black;
                border-radius: 12px;
            }

            #ScaleDisplayFrame {
                background-color: #020617;  /* dark box */
                border-radius: 16px;
            }

            #ScaleWeightLabel {
                color: #22C55E;            /* same bright green as Start Inspection */
                font-size: 24pt;
                font-weight: 600;
            }

            #ScaleOkButton {
                background-color: #2563EB;
                color: #FFFFFF;
                font-weight: 600;
                border-radius: 8px;
                border: none;
            }
            #ScaleOkButton:hover {
                background-color: #1D4ED8;
            }

            #ScaleResetButton {
                background-color: #DC2626;
                color: #FFFFFF;
                font-weight: 600;
                border-radius: 8px;
                border: none;
            }
            #ScaleResetButton:hover {
                background-color: #B91C1C;
            }
            /* Make Min/Max background BLACK inside display */
            #ScaleMinLabel, #ScaleMaxLabel {
                background-color: #020617;      /* SAME BLACK as ScaleDisplayFrame */
                color: #FACC15;                 /* Yellow text */
                padding: 2px 10px;
                border-radius: 4px;
                font-size: 12pt;
                font-weight: 600;
            }
            #ScaleDisplayFrame QWidget {
            background: transparent;
            }
            /* === Bag Details Table Styling === */
            #BagDetailsTable {
                background-color: #FFFFFF;
                border: 1px solid #CBD5E1;
                gridline-color: #E2E8F0;
                selection-background-color: #DBEAFE;
                font-size: 12pt;
            }

            /* Header */
            #BagDetailsTable::section {
                background-color: #F1F5F9;
                font-weight: bold;
                padding: 6px;
                border: none;
            }

            /* Status cells (Metal/Label/Dirty/Plastic text inside STATUS column) */
            #BagDetailsTable QTableWidgetItem {
                background-color: #CCE2E9;       /* same light-blue you wanted */
                border-radius: 6px;
                padding: 6px;
            }
            /* ===== Inspection History : Details Button ===== */
            QPushButton#DetailsButton {
                background-color: #FFFFFF;
                color: #2563EB;              /* blue text */
                border: 1px solid #2563EB;
                border-radius: 6px;
                padding: 6px 14px;
                font-weight: 600;
            }

            QPushButton#DetailsButton:hover {
                background-color: #EFF6FF;
            }


        """)

    # ------------------------ Button handlers ------------------------ #
    def _on_save_changes_clicked(self):
        """
        Save bag-level changes from this dialog into gama_qc_carton_bag:
          - remarks          → remarks
          - Remarks Recover  → recovered_remarks
          - Actions button   → actions  (e.g. 'Recover')
        """
        if not DB_ENABLED:
            QMessageBox.information(
                self,
                "DB Disabled",
                "Database saving is disabled (DB_ENABLED = False).",
            )
            return

        if not self.carton_no:
            QMessageBox.warning(
                self,
                "Missing Carton",
                "Carton number is not set for this dialog.",
            )
            return

        try:
            conn = mysql.connector.connect(
                host=DB_HOST,
                port=DB_PORT,
                user=DB_USER,
                password=DB_PASSWORD,
                database=DB_NAME,
                use_pure=True,
            )
            cur = conn.cursor()

            for row in range(self.bag_table.rowCount()):
                # --- BAG NO ---
                bag_item = self.bag_table.item(row, 0)
                if not bag_item:
                    continue
                bag_no_txt = (bag_item.text() or "").strip()
                if not bag_no_txt:
                    continue
                bag_no = int(bag_no_txt)

                # --- STATUS (still keep Failed / Passed / Pending) ---
                final_status = None
                status_item = self.bag_table.item(row, 5)
                if status_item and status_item.text():
                    final_status = status_item.text().strip()
                else:
                    # when STATUS is a pill widget (cellWidget), read from the QLabel inside
                    w_status = self.bag_table.cellWidget(row, 5)
                    if w_status is not None:
                        lbl = w_status.findChild(QLabel)
                        if lbl and lbl.text():
                            final_status = lbl.text().strip()

                # --- REMARKS ---
                remarks_item = self.bag_table.item(row, 4)
                remarks = (remarks_item.text().strip()
                           if remarks_item and remarks_item.text()
                           else None)

                # --- REMARKS RECOVER (QLineEdit) ---
                recovered_remarks = None
                rec_widget = self.bag_table.cellWidget(row, 6)
                if isinstance(rec_widget, QLineEdit):
                    txt = rec_widget.text().strip()
                    if txt:
                        recovered_remarks = txt

                # --- ACTIONS (button text: Edit / Recover / etc.) ---
                actions = None
                btn = self.bag_table.cellWidget(row, 7)
                if isinstance(btn, QPushButton):
                    txt = btn.text().strip()
                    if txt:
                        actions = txt

                # Update one row in gama_qc_carton_bag
                cur.execute(
                    f"""
                    UPDATE {DB_QC_BAG}
                    SET final_status       = %s,
                        remarks            = %s,
                        recovered_remarks  = %s,
                        actions            = %s
                    WHERE carton_no = %s AND bag_no = %s
                    """,
                    (final_status, remarks, recovered_remarks,
                     actions, self.carton_no, bag_no),
                )

            conn.commit()
            cur.close()
            conn.close()

            QMessageBox.information(
                self,
                "Saved",
                "All bag changes have been saved to MySQL.\n"
                "You can now Close this window."
            )

        except Exception as e:
            QMessageBox.critical(
                self,
                "Database Error",
                f"Failed to save changes to MySQL:\n{e}",
            )


    def _on_mark_all_passed_clicked(self):
        for row in range(self.table.rowCount()):
            w = self.table.cellWidget(row, 3)
            if isinstance(w, QLabel):
                w.setText("Passed")
                w.setStyleSheet(self._status_style("Passed"))
        QMessageBox.information(
            self,
            "Mark All as Passed",
            "All bags marked as Passed in this view.\n"
            "(Database not updated yet.)",
        )

    def _on_edit_clicked(self, row: int):
        """
        Reporting -> Bag Details:
        Click Edit -> open Digital Weight Scale
        After OK -> write locked weight into RECOVERY WEIGHT (KG) for THIS row only
        and change ACTION button to Recover.
        """
        from PySide6.QtWidgets import QDialog, QPushButton, QLineEdit, QWidget
        from PySide6.QtCore import Qt

        # ✅ Column mapping (based on your latest table header)
        COL_NO = 0
        COL_WEIGHT = 4
        COL_RECOVERY_WEIGHT = 5
        COL_STATUS = 6
        COL_REMARKS_RECOVERED = 7
        COL_ACTIONS = 8

        # 1) Bag number from table (NO column)
        bag_item = self.bag_table.item(row, COL_NO)
        bag_no = bag_item.text() if bag_item else str(row + 1)

        # 2) Dummy edit to receive the locked weight from the dialog
        dummy_edit = QLineEdit()

        # 3) Open DigitalWeightDialog (reporting mode)
        dlg = DigitalWeightDialog(
            parent=self,
            weight_edit=dummy_edit,
            card=None,
            require_bag_selection=False,
            enforce_minmax_for_ok=True,
        )

        # Title
        try:
            if hasattr(dlg, "title_label"):
                dlg.title_label.setText(f"Digital Weight Scale  •  Bag #{bag_no}")
        except Exception:
            pass

        # Min/Max from header
        try:
            if self.header_min_weight is not None:
                dlg.min_label.setText(f"Min: {float(self.header_min_weight):.2f} kg")
            else:
                dlg.min_label.setText("Min: -- kg")

            if self.header_max_weight is not None:
                dlg.max_label.setText(f"Max: {float(self.header_max_weight):.2f} kg")
            else:
                dlg.max_label.setText("Max: -- kg")
        except Exception:
            pass

        # Default selections
        try:
            dlg.metal_ok.setChecked(True)
            dlg.label_ok.setChecked(True)
            dlg.dirty_no.setChecked(True)
            dlg.plastic_ok.setChecked(True)
        except Exception:
            pass

        # 4) If OK -> push weight into RECOVERY WEIGHT column (only this row)
        if dlg.exec() == QDialog.Accepted:
            locked_txt = (dummy_edit.text() or "").strip()

            # ✅ Put the value into Recovery Weight (col 5)
            if locked_txt:
                rw_widget = self.bag_table.cellWidget(row, COL_RECOVERY_WEIGHT)
                if isinstance(rw_widget, QLineEdit):
                    rw_edit = rw_widget
                elif isinstance(rw_widget, QWidget):
                    rw_edit = rw_widget.findChild(QLineEdit)
                else:
                    rw_edit = None

                if rw_edit is None:
                    rw_edit = QLineEdit()
                    rw_edit.setPlaceholderText("0.00")
                    rw_edit.setAlignment(Qt.AlignCenter)
                    self.bag_table.setCellWidget(row, COL_RECOVERY_WEIGHT, rw_edit)

                rw_edit.setText(locked_txt)

            # ✅ Change Action button to "Recover" (ACTIONS is NOW col 8)
            act_cell = self.bag_table.cellWidget(row, COL_ACTIONS)
            act_btn = None
            if isinstance(act_cell, QPushButton):
                act_btn = act_cell
            elif isinstance(act_cell, QWidget):
                act_btn = act_cell.findChild(QPushButton)

            if act_btn is not None:
                act_btn.setText("Recover")
                act_btn.setObjectName("RecoverButton")
                act_btn.setEnabled(True)
                act_btn.setStyleSheet("")  # let your global stylesheet apply





class FailedCartonsPage(QWidget):
    """
    Standalone Reporting page: Failed Cartons Overview.
    """

    def __init__(self, parent=None, open_new_inspection_callback=None):
        super().__init__(parent)

        # Needed by styles (#ReportingPage in stylesheet)
        self.setObjectName("ReportingPage")

        # Callback to open QC form (MainDashboardWindow.show_qc_form)
        self.open_new_inspection_callback = open_new_inspection_callback

        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 32, 40, 40)
        outer.setSpacing(0)

        # Big white panel like web design
        panel = QFrame()
        panel.setObjectName("ReportingPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(24, 24, 24, 24)
        panel_layout.setSpacing(16)

        # --- Header row: title + button ---
        header_row = QHBoxLayout()

        rep_title = QLabel("Failed Cartons Overview")
        rep_title.setObjectName("FailedCartonsTitle")
        header_row.addWidget(rep_title)

        header_row.addStretch()

        panel_layout.addLayout(header_row)

        # --- Scroll area for cards ---
        self.failed_scroll = QScrollArea()
        self.failed_scroll.setWidgetResizable(True)
        panel_layout.addWidget(self.failed_scroll)

        failed_body = QWidget()
        failed_body.setObjectName("FailedCartonContainer")
        self.failed_scroll.setWidget(failed_body)

        self.failed_root_layout = QVBoxLayout(failed_body)
        self.failed_root_layout.setContentsMargins(0, 0, 0, 0)
        self.failed_root_layout.setSpacing(16)

        # Grid for 3-per-row cards
        self.failed_grid = QGridLayout()
        self.failed_grid.setSpacing(16)
        self.failed_root_layout.addLayout(self.failed_grid)

        # Message when nothing to show
        self.no_failed_label = QLabel("No failed cartons found.")
        self.no_failed_label.setObjectName("CardSubtitle")
        self.failed_root_layout.addWidget(self.no_failed_label)

        outer.addWidget(panel)

    # ------------------------------------------------------------------ #
    #  Refresh data from MySQL and build cards
    # ------------------------------------------------------------------ #
    def refresh_failed_cartons(self):
        """
        Load failed cartons from gama_qc_carton_header and build cards
        using a 3-per-row layout (like the web design).

        Extra:
        - Red pill: "<n> Failed"
        - Yellow pill: "Pending Recovery"  (some failed bags NOT yet Recover)
        - Green pill: "Recovered"         (all failed bags are Recover)
        """

        # 1) Clear old cards
        while self.failed_grid.count():
            item = self.failed_grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        records = []

        # ---------- Helper: check recovery status for this carton ----------
        def get_recovery_flags(carton_no: str, qc_no: str, total_failed: int):
            """
            Returns (pending_recovery, all_recovered)

            pending_recovery = at least one failed bag with actions <> 'Recover'
            all_recovered    = all failed bags have actions = 'Recover'
            """
            if not DB_ENABLED or total_failed <= 0:
                return False, False

            try:
                conn = mysql.connector.connect(
                    host=DB_HOST,
                    port=DB_PORT,
                    user=DB_USER,
                    password=DB_PASSWORD,
                    database=DB_NAME,
                    use_pure=True,
                )
                cur = conn.cursor()

                cur.execute(
                    f"""
                    SELECT
                        COUNT(*),
                        SUM(CASE WHEN actions = 'Recover' THEN 1 ELSE 0 END)
                    FROM {DB_QC_BAG}
                    WHERE carton_no = %s
                      AND qc_no     = %s
                      AND final_status IN ('Failed','FAIL')
                    """,
                    (carton_no, qc_no),
                )
                row = cur.fetchone() or (0, 0)

                cur.close()
                conn.close()

                failed_cnt = row[0] or 0
                recovered_cnt = row[1] or 0

                pending_recovery = failed_cnt > 0 and recovered_cnt < failed_cnt
                all_recovered = failed_cnt > 0 and recovered_cnt == failed_cnt
                return pending_recovery, all_recovered

            except Exception as e:
                print("[WARN] get_recovery_flags:", e)
                return False, False

        # 2) Load from MySQL
        if DB_ENABLED:
            try:
                conn = mysql.connector.connect(
                    host=DB_HOST,
                    port=DB_PORT,
                    user=DB_USER,
                    password=DB_PASSWORD,
                    database=DB_NAME,
                    use_pure=True,
                )
                cur = conn.cursor()

                # Only cartons with failed bags or overall FAIL
                cur.execute(
                    f"""
                    SELECT
                        carton_no,        -- 0
                        qc_no,            -- 1
                        check_by,         -- 2
                        total_bags,       -- 3
                        total_passed,     -- 4
                        total_failed,     -- 5
                        box_remarks       -- 6
                    FROM {DB_QC_HEADER}
                    WHERE total_failed > 0 OR overall_result = 'FAIL'
                    ORDER BY created_at DESC
                    LIMIT 60
                    """
                )
                records = cur.fetchall()

                cur.close()
                conn.close()
            except Exception as e:
                print("[ERROR] Failed to load failed cartons:", e)

        # 3) Show / hide "no failed cartons" label
        has_data = bool(records)
        self.no_failed_label.setVisible(not has_data)
        if not has_data:
            return

        # 4) Layout config
        self.failed_grid.setColumnStretch(0, 1)
        self.failed_grid.setColumnStretch(1, 1)
        self.failed_grid.setColumnStretch(2, 1)

        # 5) Helper to build single card
        def make_failed_card(idx, carton_no, qc_no, check_by,
                             total_bags, total_pass, total_fail, remarks,
                             pending_recovery, all_recovered):

            card = QFrame()
            card.setObjectName("FailedCartonCard")

            cl = QVBoxLayout(card)
            cl.setContentsMargins(20, 16, 20, 16)
            cl.setSpacing(10)

            # --- TOP: Title + Failed Pill + Pending/Recovered Pill ---
            top = QHBoxLayout()

            title = QLabel(f"CTN - {carton_no}")
            title.setObjectName("FailedCartonTitle")
            top.addWidget(title)

            top.addStretch()

            pill = QLabel(f"{total_fail} Failed")
            pill.setObjectName("FailedCartonPill")
            top.addWidget(pill)

            # Yellow / Green pill
            if pending_recovery:
                state_lbl = QLabel("Pending Recovery")
                state_lbl.setObjectName("PendingRecoveryPill")
                top.addWidget(state_lbl)
            elif all_recovered:
                state_lbl = QLabel("Recovered")
                state_lbl.setObjectName("RecoveredPill")
                top.addWidget(state_lbl)

            cl.addLayout(top)

            # Subtitle: QC number
            subtitle = QLabel(f"QC: {qc_no or '-'}")
            subtitle.setObjectName("FailedCartonSubtitle")
            cl.addWidget(subtitle)

            # --- Small rows (icon + label + value) ---
            def add_row(icon, label, value):
                row = QHBoxLayout()

                icon_lbl = QLabel(icon)
                icon_lbl.setObjectName("IconLabel")
                row.addWidget(icon_lbl)

                lbl = QLabel(label)
                lbl.setObjectName("FailedCartonRowLabel")
                row.addWidget(lbl)

                row.addStretch()

                val = QLabel(value)
                val.setObjectName("FailedCartonRowValue")
                row.addWidget(val)

                cl.addLayout(row)

            add_row("👤", "Checked By:", check_by or "-")
            add_row("📦", "Total Bags:", str(total_bags or 0))
            add_row("✅", "Passed:", str(total_pass or 0))
            add_row("⚠️", "Failed:", str(total_fail or 0))

            # Box remarks
            remarks_label = QLabel("Box Remarks:")
            remarks_label.setObjectName("FailedCartonRowLabel")
            cl.addWidget(remarks_label)

            remarks_box = QLabel(remarks or "-")
            remarks_box.setWordWrap(True)
            remarks_box.setObjectName("FailedCartonRemarksBox")
            cl.addWidget(remarks_box)

            # Hint text
            hint = QLabel("Double click to view bag details")
            hint.setObjectName("FailedCartonHint")
            cl.addWidget(hint)

            cl.addStretch()

            # --- Double-click → open Bag Details dialog ---
            def _on_double_click(event, c=carton_no, q=qc_no):
                root = self.window()
                effect = QGraphicsOpacityEffect(root)
                effect.setOpacity(0.25)
                root.setGraphicsEffect(effect)

                try:
                    dlg = BagDetailsDialog(
                        parent=self,
                        carton_no=c,
                        qc_no=q,
                    )
                    dlg.exec()
                finally:
                    root.setGraphicsEffect(None)

                # after operator changes to Recover, refresh pills
                self.refresh_failed_cartons()

            card.mouseDoubleClickEvent = _on_double_click

            return card

        # 6) Add cards into grid 3 per row
        for idx, row in enumerate(records):
            carton_no, qc_no, check_by, total_bags, total_pass, total_fail, remarks = row

            pending_recovery, all_recovered = get_recovery_flags(
                carton_no, qc_no, total_fail or 0
            )

            r = idx // 3
            c = idx % 3
            card = make_failed_card(
                idx + 1,
                carton_no, qc_no, check_by,
                total_bags, total_pass, total_fail, remarks,
                pending_recovery, all_recovered,
            )
            self.failed_grid.addWidget(card, r, c)




# ------------------- Main Dashboard Window ------------------- #



class PerformanceDashboardPage(QWidget):
    """Inspection Performance Dashboard (Configuration → Reports & Analytics)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PerfDashboardPage")

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        # ----- Top bar -----
        top = QHBoxLayout()
        top.setSpacing(12)

        title_col = QVBoxLayout()
        title_col.setSpacing(4)
        title = QLabel("Inspection Performance Dashboard")
        title.setObjectName("PerfTitle")
        subtitle = QLabel("Comprehensive analytics with predictive insights and real-time monitoring")
        subtitle.setObjectName("PerfSubtitle")
        title_col.addWidget(title)
        title_col.addWidget(subtitle)

        top.addLayout(title_col)
        top.addStretch()

        self.range_combo = QComboBox()
        self.range_combo.setObjectName("PerfRangeCombo")
        self.range_combo.addItems(["Today", "Last 7 Days", "Last 30 Days"])
        self.range_combo.setCurrentText("Last 7 Days")
        self.range_combo.currentTextChanged.connect(self.refresh)

        self.filter_btn = QPushButton("Advanced Filters")
        self.filter_btn.setObjectName("PerfFilterButton")
        self.filter_btn.clicked.connect(lambda: QMessageBox.information(self, "Advanced Filters", "Coming soon."))



        data_range_lbl = QLabel("Date Range:")
        data_range_lbl.setObjectName("PerfTopLabel")
        top.addWidget(data_range_lbl)
        top.addWidget(self.range_combo)
        top.addSpacing(8)
        top.addWidget(self.filter_btn)


        root.addLayout(top)

        # ----- Cards row -----
        cards = QHBoxLayout()
        cards.setSpacing(18)

        self.total_main = QLabel("0")
        self.total_main.setObjectName("PerfMainValue")

        # --- improvement row (↑ 14.2% vs previous period) ---
        self.total_improve_arrow = QLabel("↑")
        self.total_improve_arrow.setObjectName("PerfImproveArrow")

        # make arrow bigger (same idea you used for pass rate)
        font = self.total_improve_arrow.font()
        font.setPointSize(18)
        font.setWeight(QFont.Bold)
        self.total_improve_arrow.setFont(font)

        self.total_improve_pct = QLabel("0.0%")
        self.total_improve_pct.setObjectName("PerfImprovePct")

        self.total_improve_text = QLabel("vs previous\nperiod")
        self.total_improve_text.setObjectName("PerfImproveText")
        self.total_improve_text.setWordWrap(True)

        total_improve_row = QWidget()
        total_improve_row.setObjectName("PerfImproveRow")
        total_improve_row.setAttribute(Qt.WA_StyledBackground, True)

        til = QHBoxLayout(total_improve_row)
        til.setContentsMargins(0, 0, 0, 0)
        til.setSpacing(8)
        til.addWidget(self.total_improve_arrow)
        til.addWidget(self.total_improve_pct)
        til.addSpacing(8)
        til.addWidget(self.total_improve_text)
        til.addStretch()

        # dummy sub label (hidden via QSS)
        dummy_sub = QLabel("")
        dummy_sub.hide()
        dummy_sub.setFixedHeight(0)
        dummy_sub.setObjectName("PerfSubValue")

        total_card = self._make_card(
            "TOTAL\nINSPECTIONS",
            self.total_main,
            dummy_sub,
            icon_text="📋",
            accent="green",
            extra_widget=total_improve_row
        )

        self.pass_main = QLabel("0.0%")
        self.pass_main.setObjectName("PerfMainValue")

        # --- improvement row (↑ 2.3% improvement) ---
        self.pass_improve_arrow = QLabel("↑")
        self.pass_improve_arrow.setObjectName("PerfImproveArrow")

        font = self.pass_improve_arrow.font()
        font.setPointSize(18)          # 🔥 BIG arrow
        font.setWeight(QFont.Bold)
        self.pass_improve_arrow.setFont(font)


        self.pass_improve_pct = QLabel("0.0%")
        self.pass_improve_pct.setObjectName("PerfImprovePct")

        self.pass_improve_text = QLabel("improvement")
        self.pass_improve_text.setObjectName("PerfImproveText")

        improve_row = QWidget()
        improve_row.setObjectName("PerfImproveRow")
        improve_row.setAttribute(Qt.WA_StyledBackground, True)

        improve_l = QHBoxLayout(improve_row)
        improve_l.setContentsMargins(0, 0, 0, 0)
        improve_l.setSpacing(8)
        improve_l.addWidget(self.pass_improve_arrow)
        improve_l.addWidget(self.pass_improve_pct)
        improve_l.addSpacing(6)
        improve_l.addWidget(self.pass_improve_text)
        improve_l.addStretch()

        # --- progress bar like picture ---
        self.pass_bar = QProgressBar()
        self.pass_bar.setRange(0, 100)
        self.pass_bar.setValue(0)
        self.pass_bar.setTextVisible(False)
        self.pass_bar.setObjectName("PerfProgress")

        # We'll re-use _make_card(), but pass improvement row as "sub" area:
        # (so the card shows: title, value, improvement row, progress bar)
        dummy_sub = QLabel("")  # not shown (we’ll hide via QSS)
        dummy_sub.setObjectName("PerfSubValue")

        pass_card = self._make_card(
            "PASS RATE",
            self.pass_main,
            dummy_sub,
            icon_text="✔️",          # looks like the double-check
            accent="green",
            extra_widget=improve_row # first extra widget
        )

        # Add progress bar under improvement row (inside the same card)
        # easiest: just add it after card creation by finding the card layout:
        pass_card.layout().addWidget(self.pass_bar)


        self.fail_main = QLabel("0")
        self.recover_main = QLabel("0")
        self.recovery_rate = QLabel("0.0% Recovery")
        fail_card = self._make_split_card()


        # ===== AVG INSPECTION TIME UI =====
        self.avg_time_main = QLabel("—")
        self.avg_time_main.setObjectName("PerfBigNumber")

        # Trend row widgets (↓ 1.2min faster)
        self.avg_arrow = QLabel("↓")
        self.avg_arrow.setObjectName("PerfTrendArrow")

        self.avg_delta = QLabel("0.0min")
        self.avg_delta.setObjectName("PerfTrendValue")

        self.avg_word = QLabel("faster")
        self.avg_word.setObjectName("PerfTrendText")

        trend_row = QWidget()
        trend_row.setObjectName("PerfTrendRow")
        trend_row.setAttribute(Qt.WA_StyledBackground, True)
        trend_row.setStyleSheet("background: transparent;")

        tr = QHBoxLayout(trend_row)
        tr.setContentsMargins(0, 0, 0, 0)
        tr.setSpacing(10)

        tr.addWidget(self.avg_arrow)
        tr.addWidget(self.avg_delta)
        tr.addSpacing(10)
        tr.addWidget(self.avg_word)
        tr.addStretch()

        # Efficiency line
        self.avg_eff = QLabel("Efficiency: —")
        self.avg_eff.setObjectName("PerfFooterText")

        dummy_sub = QLabel("")  # placeholder, not used

        avg_card = self._make_card(
            "AVG INSPECTION\nTIME",
            self.avg_time_main,
            dummy_sub,
            icon_text="⏱️",
            accent="blue",
            extra_widget=trend_row
        )

        avg_card.layout().addWidget(self.avg_eff)

        cards.addWidget(total_card)
        cards.addWidget(pass_card)
        cards.addWidget(fail_card)
        cards.addWidget(avg_card)

        root.addLayout(cards)
        root.addStretch()

        # Real-time refresh timer
        self._timer = QTimer(self)
        self._timer.setInterval(2500)   # 2.5s refresh
        self._timer.timeout.connect(self.refresh)

        # first load
        QTimer.singleShot(0, self.refresh)

    def _make_card(self, header_text, main_lbl, sub_lbl, icon_text="", accent="green", extra_widget=None, footer_text=None):
        card = QFrame()
        card.setObjectName("PerfCard")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(18, 16, 18, 16)
        cl.setSpacing(10)

        # header row
        hr = QHBoxLayout()
        ht = QLabel(header_text)
        ht.setObjectName("PerfCardHeader")
        ht.setTextFormat(Qt.PlainText)
        ht.setWordWrap(True)
        hr.addWidget(ht)

        hr.addStretch()
        icon = QLabel(icon_text)
        icon.setObjectName("PerfCardIcon")
        hr.addWidget(icon)
        cl.addLayout(hr)

        main_lbl.setObjectName("PerfMainValue")
        sub_lbl.setObjectName("PerfSubValue")
        cl.addWidget(main_lbl)
        cl.addWidget(sub_lbl)

        if extra_widget is not None:
            cl.addWidget(extra_widget)

        if footer_text:
            footer = QLabel(footer_text)
            footer.setObjectName("PerfFooter")
            cl.addWidget(footer)

        return card

    def _make_split_card(self):
        card = QFrame()
        card.setObjectName("PerfCard")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(18, 16, 18, 16)
        cl.setSpacing(10)

        hr = QHBoxLayout()
        ht = QLabel("FAILED &\nRECOVERED")
        ht.setObjectName("PerfCardHeader")
        ht.setWordWrap(True)
        hr.addWidget(ht)
        hr.addStretch()
        icon = QLabel("🔁")
        icon.setObjectName("PerfCardIcon")
        hr.addWidget(icon)
        cl.addLayout(hr)

        row = QHBoxLayout()
        row.setSpacing(14)

        left = QVBoxLayout()
        self.fail_main.setObjectName("PerfMainValueFail")
        fail_cap = QLabel("Failed")
        fail_cap.setObjectName("PerfMiniCaption")
        left.addWidget(self.fail_main)
        left.addWidget(fail_cap)

        right = QVBoxLayout()
        self.recover_main.setObjectName("PerfMainValueRecover")
        rec_cap = QLabel("Recovered")
        rec_cap.setObjectName("PerfMiniCaption")
        right.addWidget(self.recover_main)
        right.addWidget(rec_cap)

        row.addLayout(left)
        row.addLayout(right)
        row.addStretch()
        cl.addLayout(row)

        self.recovery_rate.setObjectName("PerfRecoveryRate")
        cl.addWidget(self.recovery_rate)

        return card



    def start_monitoring(self):
        if not self._timer.isActive():
            self._timer.start()

    def stop_monitoring(self):
        if self._timer.isActive():
            self._timer.stop()

    def _days_for_range(self):
        t = self.range_combo.currentText().strip()
        if t == "Today":
            return 1
        if t == "Last 30 Days":
            return 30
        return 7

    def refresh(self):
        """Refresh numbers from MySQL (real-time)."""
        days = self._days_for_range()
        t = self.range_combo.currentText().strip()
        is_today = (t == "Today")

        # defaults (so UI doesn't crash if DB fails)
        total = passed = failed = recovered = 0
        prev_total = 0

        cur_avg_min = None
        prev_avg_min = None

        if DB_ENABLED:
            try:
                conn = mysql.connector.connect(
                    host=DB_HOST,
                    port=DB_PORT,
                    user=DB_USER,
                    password=DB_PASSWORD,
                    database=DB_NAME,
                    use_pure=True,
                )
                cur = conn.cursor()

                # ==========================================================
                # ✅ CURRENT WINDOW totals (PASS/FAIL/TOTAL)
                # ==========================================================
                if is_today:
                    cur.execute(
                        f"""
                        SELECT
                            COALESCE(SUM(CASE WHEN overall_result='PASS' THEN 1 ELSE 0 END), 0) AS pass_cnt,
                            COALESCE(SUM(CASE WHEN overall_result='FAIL' THEN 1 ELSE 0 END), 0) AS fail_cnt,
                            COUNT(*) AS total_cnt
                        FROM {DB_QC_HEADER}
                        WHERE created_at >= CONCAT(CURDATE(), ' 00:00:00')
                        """
                    )
                else:
                    cur.execute(
                        f"""
                        SELECT
                            COALESCE(SUM(CASE WHEN overall_result='PASS' THEN 1 ELSE 0 END), 0) AS pass_cnt,
                            COALESCE(SUM(CASE WHEN overall_result='FAIL' THEN 1 ELSE 0 END), 0) AS fail_cnt,
                            COUNT(*) AS total_cnt
                        FROM {DB_QC_HEADER}
                        WHERE created_at >= (NOW() - INTERVAL %s DAY)
                        """,
                        (days,)
                    )

                row = cur.fetchone()
                if row:
                    passed = int(row[0] or 0)
                    failed = int(row[1] or 0)
                    total  = int(row[2] or 0)

                # ==========================================================
                # ✅ PREVIOUS WINDOW total (for TOTAL INSPECTIONS change %)
                # Today -> Yesterday (00:00 to 00:00)
                # Others -> rolling previous window
                # ==========================================================
                if is_today:
                    cur.execute(
                        f"""
                        SELECT COUNT(*)
                        FROM {DB_QC_HEADER}
                        WHERE created_at >= CONCAT(CURDATE() - INTERVAL 1 DAY, ' 00:00:00')
                        AND created_at <  CONCAT(CURDATE(), ' 00:00:00')
                        """
                    )
                else:
                    cur.execute(
                        f"""
                        SELECT COUNT(*)
                        FROM {DB_QC_HEADER}
                        WHERE created_at >= (NOW() - INTERVAL %s DAY)
                        AND created_at <  (NOW() - INTERVAL %s DAY)
                        """,
                        (days * 2, days)
                    )

                row_prev = cur.fetchone()
                prev_total = int(row_prev[0] or 0) if row_prev else 0

                # ==========================================================
                # ✅ RECOVERED bags
                # ==========================================================
                if is_today:
                    cur.execute(
                        f"""
                        SELECT COUNT(*)
                        FROM {DB_QC_BAG}
                        WHERE created_at >= CONCAT(CURDATE(), ' 00:00:00')
                        AND LOWER(COALESCE(actions, '')) = 'recover'
                        """
                    )
                else:
                    cur.execute(
                        f"""
                        SELECT COUNT(*)
                        FROM {DB_QC_BAG}
                        WHERE created_at >= (NOW() - INTERVAL %s DAY)
                        AND LOWER(COALESCE(actions, '')) = 'recover'
                        """,
                        (days,)
                    )

                row2 = cur.fetchone()
                recovered = int(row2[0] or 0) if row2 else 0

                # ==========================================================
                # ✅ AVG INSPECTION TIME (CURRENT window)
                # ==========================================================
                if is_today:
                    cur.execute(
                        f"""
                        SELECT AVG(TIMESTAMPDIFF(SECOND, started_at, completed_at))
                        FROM {DB_QC_HEADER}
                        WHERE created_at >= CONCAT(CURDATE(), ' 00:00:00')
                        AND started_at IS NOT NULL
                        AND completed_at IS NOT NULL
                        AND completed_at > started_at
                        """
                    )
                else:
                    cur.execute(
                        f"""
                        SELECT AVG(TIMESTAMPDIFF(SECOND, started_at, completed_at))
                        FROM {DB_QC_HEADER}
                        WHERE created_at >= (NOW() - INTERVAL %s DAY)
                        AND started_at IS NOT NULL
                        AND completed_at IS NOT NULL
                        AND completed_at > started_at
                        """,
                        (days,)
                    )

                cur_avg_sec = cur.fetchone()[0]
                cur_avg_sec = float(cur_avg_sec) if cur_avg_sec is not None else None
                cur_avg_min = (cur_avg_sec / 60.0) if (cur_avg_sec is not None) else None

                # ==========================================================
                # ✅ AVG INSPECTION TIME (PREVIOUS window)
                # Today -> Yesterday window
                # ==========================================================
                if is_today:
                    cur.execute(
                        f"""
                        SELECT AVG(TIMESTAMPDIFF(SECOND, started_at, completed_at))
                        FROM {DB_QC_HEADER}
                        WHERE created_at >= CONCAT(CURDATE() - INTERVAL 1 DAY, ' 00:00:00')
                        AND created_at <  CONCAT(CURDATE(), ' 00:00:00')
                        AND started_at IS NOT NULL
                        AND completed_at IS NOT NULL
                        AND completed_at > started_at
                        """
                    )
                else:
                    cur.execute(
                        f"""
                        SELECT AVG(TIMESTAMPDIFF(SECOND, started_at, completed_at))
                        FROM {DB_QC_HEADER}
                        WHERE created_at >= (NOW() - INTERVAL %s DAY)
                        AND created_at <  (NOW() - INTERVAL %s DAY)
                        AND started_at IS NOT NULL
                        AND completed_at IS NOT NULL
                        AND completed_at > started_at
                        """,
                        (days * 2, days)
                    )

                prev_avg_sec = cur.fetchone()[0]
                prev_avg_sec = float(prev_avg_sec) if prev_avg_sec is not None else None
                prev_avg_min = (prev_avg_sec / 60.0) if (prev_avg_sec is not None) else None

                cur.close()
                conn.close()

            except Exception as e:
                print("[WARN] PerformanceDashboardPage.refresh DB error:", e)

        # ==================== UPDATE UI (always runs) ====================

        # ---- TOTAL INSPECTIONS ----
        self.total_main.setText(f"{total:,}")

        if prev_total > 0:
            change = ((total - prev_total) / prev_total) * 100.0
        else:
            change = 100.0 if total > 0 else 0.0

        self.total_improve_arrow.setText("↑" if change >= 0 else "↓")
        self.total_improve_pct.setText(f"{abs(change):.1f}%")
        self.total_improve_text.setText("vs previous\nperiod")

        # ---- PASS RATE ----
        pass_rate = (passed / total * 100.0) if total else 0.0
        self.pass_main.setText(f"{pass_rate:.1f}%")

        # improvement (still placeholder until you add previous pass rate query)
        improvement = 2.3 if pass_rate > 0 else 0.0
        self.pass_improve_pct.setText(f"{improvement:.1f}%")

        # progress bar
        try:
            self.pass_bar.setValue(int(round(pass_rate)))
        except Exception:
            pass

        # ---- FAILED / RECOVERED ----
        self.fail_main.setText(f"{failed:,}")
        self.recover_main.setText(f"{recovered:,}")

        rec_rate = (recovered / failed * 100.0) if failed else 0.0
        self.recovery_rate.setText(f"⟳ {rec_rate:.1f}% Recovery")

        # ---- AVG INSPECTION TIME ----
        if cur_avg_min is not None and cur_avg_min > 0:
            self.avg_time_main.setText(f"{cur_avg_min:.1f}min")
        else:
            self.avg_time_main.setText("—")

        # trend + efficiency
        if (cur_avg_min is not None and cur_avg_min > 0) and (prev_avg_min is not None and prev_avg_min > 0):
            delta = prev_avg_min - cur_avg_min  # positive = faster

            if delta >= 0:
                self.avg_arrow.setText("↓")
                self.avg_word.setText("faster")
            else:
                self.avg_arrow.setText("↑")
                self.avg_word.setText("slower")

            self.avg_delta.setText(f"{abs(delta):.1f}min")

            # Efficiency vs previous (cap 0..100)
            eff = (cur_avg_min / prev_avg_min) * 100.0
            eff = max(0.0, min(100.0, eff))
            self.avg_eff.setText(f"Efficiency: {eff:.0f}%")
        else:
            self.avg_arrow.setText("—")
            self.avg_delta.setText("—")
            self.avg_word.setText("")
            self.avg_eff.setText("Efficiency: —")





class InspectionHistoryPage(QWidget):
    """Inspection History page: filters + paginated inspection records."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("InspectionHistoryPage")

        self._page = 1
        self._total_records = 0
        self._per_page = 10

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        # ---- Title ----
        title = QLabel("Inspection History")
        title.setObjectName("HistTitle")
        subtitle = QLabel("Quality Control Inspection Records.")
        subtitle.setObjectName("HistSubtitle")
        subtitle.setWordWrap(True)

        root.addWidget(title)
        root.addWidget(subtitle)

        # ---- Filters card ----
        self.filters_card = QFrame()
        self.filters_card.setObjectName("HistCard")
        self.filters_card.setProperty("variant", "filters")
        self.filters_card.setAttribute(Qt.WA_StyledBackground, True)
        f_lay = QVBoxLayout(self.filters_card)
        f_lay.setContentsMargins(18, 16, 18, 16)
        f_lay.setSpacing(14)

        header_row = QHBoxLayout()
        header_row.setSpacing(10)
        hdr = QLabel("Inspection Filters")
        hdr.setObjectName("HistCardTitle")
        header_row.addWidget(hdr)
        header_row.addStretch()

        self.reset_filters_btn = QPushButton("Reset All Filters")
        self.reset_filters_btn.setObjectName("HistLinkButton")
        self.reset_filters_btn.setCursor(Qt.PointingHandCursor)
        self.reset_filters_btn.clicked.connect(self.reset_filters)
        header_row.addWidget(self.reset_filters_btn)
        f_lay.addLayout(header_row)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(12)

        def field_box(label_text: str, icon: str = ""):
            box = QFrame()
            box.setObjectName("HistFieldBox")
            box.setAttribute(Qt.WA_StyledBackground, True)
            bl = QVBoxLayout(box)
            bl.setContentsMargins(14, 12, 14, 12)
            bl.setSpacing(6)

            top = QHBoxLayout()
            top.setSpacing(6)
            if icon:
                ic = QLabel(icon)
                ic.setObjectName("HistFieldIcon")
                top.addWidget(ic)
            lbl = QLabel(label_text)
            lbl.setObjectName("HistFieldLabel")
            top.addWidget(lbl)
            top.addStretch()
            bl.addLayout(top)
            return box, bl

        # Year
        box, bl = field_box("Select Year", "📅")
        self.year_combo = QComboBox()
        self.year_combo.setObjectName("HistCombo")
        bl.addWidget(self.year_combo)
        grid.addWidget(box, 0, 0)

        # Month
        box, bl = field_box("Select Month", "🗓️")
        self.month_combo = QComboBox()
        self.month_combo.setObjectName("HistCombo")
        bl.addWidget(self.month_combo)
        grid.addWidget(box, 0, 1)

        # Inspector
        box, bl = field_box("Inspector Name", "👤")
        self.inspector_combo = QComboBox()
        self.inspector_combo.setObjectName("HistCombo")
        bl.addWidget(self.inspector_combo)
        grid.addWidget(box, 1, 0, 1, 2)

        f_lay.addLayout(grid)

        bottom_row = QHBoxLayout()
        bottom_row.addStretch()
        self.apply_btn = QPushButton("Apply Filters")
        self.apply_btn.setObjectName("HistPrimaryButton")
        self.apply_btn.setCursor(Qt.PointingHandCursor)
        self.apply_btn.clicked.connect(lambda: self.load_records(page=1))
        bottom_row.addWidget(self.apply_btn)
        f_lay.addLayout(bottom_row)

        root.addWidget(self.filters_card)

        # ---- Records card ----
        self.records_card = QFrame()
        self.records_card.setObjectName("HistCard")
        self.records_card.setProperty("variant", "records")
        self.records_card.setAttribute(Qt.WA_StyledBackground, True)
        r_lay = QVBoxLayout(self.records_card)
        r_lay.setContentsMargins(18, 16, 18, 16)
        r_lay.setSpacing(12)

        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        rec_hdr = QLabel("Inspection Records")
        rec_hdr.setObjectName("HistCardTitle")
        top_row.addWidget(rec_hdr)
        top_row.addStretch()

        self.count_lbl = QLabel("Showing 0 of 0 records")
        self.count_lbl.setObjectName("HistSmallText")
        top_row.addWidget(self.count_lbl)

        self.per_page_combo = QComboBox()
        self.per_page_combo.setObjectName("HistComboSmall")
        self.per_page_combo.addItems(["10 per page", "20 per page", "50 per page"])
        self.per_page_combo.currentTextChanged.connect(self._on_per_page_changed)
        top_row.addWidget(self.per_page_combo)
        r_lay.addLayout(top_row)

        self.table = QTableWidget(0, 6)
        self.table.setObjectName("HistTable")
        self.table.setHorizontalHeaderLabels(["DATE & TIME", "INSPECTOR", "CARTON NUMBER", "STATUS", "RECOVERY", "DETAILS"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        hdr = self.table.horizontalHeader()
        hdr.setStretchLastSection(False)
        hdr.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # DATE & TIME
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # INSPECTOR
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)           # CARTON NUMBER (take remaining space)

        hdr.setSectionResizeMode(3, QHeaderView.Fixed)             # STATUS
        hdr.setSectionResizeMode(4, QHeaderView.Fixed)             # RECOVERY
        hdr.setSectionResizeMode(5, QHeaderView.Fixed)             # DETAILS

        self.table.setColumnWidth(3, 140)
        self.table.setColumnWidth(4, 160)
        self.table.setColumnWidth(5, 120)
        
        self.table.verticalHeader().setDefaultSectionSize(44)      # nicer row height
        r_lay.addWidget(self.table)

        pager = QHBoxLayout()
        pager.setSpacing(10)
        self.page_lbl = QLabel("Page 1 of 1")
        self.page_lbl.setObjectName("HistSmallText")
        pager.addWidget(self.page_lbl)
        pager.addStretch()

        self.prev_btn = QPushButton("Previous")
        self.prev_btn.setObjectName("HistGhostButton")
        self.prev_btn.clicked.connect(self.prev_page)
        self.next_btn = QPushButton("Next")
        self.next_btn.setObjectName("HistGhostButton")
        self.next_btn.clicked.connect(self.next_page)
        pager.addWidget(self.prev_btn)
        pager.addWidget(self.next_btn)
        r_lay.addLayout(pager)

        root.addWidget(self.records_card, 1)

        # initial options + load
        self.reload_filter_options()
        self.load_records(page=1)

    # ---------------- Filters ---------------- #
    def reset_filters(self):
        self.year_combo.setCurrentIndex(0)
        self.month_combo.setCurrentIndex(0)
        self.inspector_combo.setCurrentIndex(0)
        self.load_records(page=1)

    def reload_filter_options(self):
        # defaults
        self.year_combo.blockSignals(True)
        self.month_combo.blockSignals(True)
        self.inspector_combo.blockSignals(True)

        self.year_combo.clear()
        self.month_combo.clear()
        self.inspector_combo.clear()

        self.year_combo.addItem("All Years", None)
        self.month_combo.addItem("All Months", None)
        self.inspector_combo.addItem("All Inspectors", None)

        if not DB_ENABLED:
            self.year_combo.blockSignals(False)
            self.month_combo.blockSignals(False)
            self.inspector_combo.blockSignals(False)
            return

        try:
            conn = mysql.connector.connect(
                host=DB_HOST,
                port=DB_PORT,
                user=DB_USER,
                password=DB_PASSWORD,
                database=DB_NAME,
                use_pure=True,
            )
            cur = conn.cursor()

            # Years
            cur.execute(f"SELECT DISTINCT YEAR(created_at) FROM {DB_QC_HEADER} ORDER BY YEAR(created_at) DESC")
            for (y,) in cur.fetchall() or []:
                if y:
                    self.year_combo.addItem(str(y), int(y))

            # Inspectors
            cur.execute(f"SELECT DISTINCT check_by FROM {DB_QC_HEADER} WHERE check_by IS NOT NULL AND check_by<>'' ORDER BY check_by ASC")
            for (name,) in cur.fetchall() or []:
                nm = (name or "").strip()
                if nm:
                    self.inspector_combo.addItem(nm, nm)

            cur.close()
            conn.close()
        except Exception as e:
            print("[WARN] reload_filter_options:", e)

        self.year_combo.blockSignals(False)
        self.month_combo.blockSignals(False)
        self.inspector_combo.blockSignals(False)

    def _on_per_page_changed(self, txt: str):
        try:
            n = int((txt or "").split()[0])
        except Exception:
            n = 10
        self._per_page = max(1, n)
        self.load_records(page=1)

    # ---------------- Paging ---------------- #
    def prev_page(self):
        if self._page > 1:
            self.load_records(page=self._page - 1)

    def next_page(self):
        total_pages = max(1, (self._total_records + self._per_page - 1) // self._per_page)
        if self._page < total_pages:
            self.load_records(page=self._page + 1)

    # ---------------- Data loading ---------------- #
    def _recovery_text(self, carton_no: str, qc_no: str, total_failed: int):
        if not DB_ENABLED or not carton_no or not qc_no or (total_failed or 0) <= 0:
            return "-"
        try:
            conn = mysql.connector.connect(
                host=DB_HOST,
                port=DB_PORT,
                user=DB_USER,
                password=DB_PASSWORD,
                database=DB_NAME,
                use_pure=True,
            )
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT
                    COUNT(*),
                    SUM(CASE WHEN LOWER(COALESCE(actions,'')) = 'recover' THEN 1 ELSE 0 END)
                FROM {DB_QC_BAG}
                WHERE carton_no = %s
                  AND qc_no     = %s
                  AND final_status IN ('Failed','FAIL')
                """,
                (carton_no, qc_no),
            )
            row = cur.fetchone() or (0, 0)
            cur.close()
            conn.close()
            failed_cnt = int(row[0] or 0)
            recovered_cnt = int(row[1] or 0)
            if failed_cnt <= 0:
                return "-"
            return "Recovered" if recovered_cnt == failed_cnt else "Pending"
        except Exception as e:
            print("[WARN] _recovery_text:", e)
            return "-"

    def load_records(self, page: int = 1):
        self._page = max(1, int(page or 1))
        self.table.setRowCount(0)
        self._total_records = 0

        # Filters
        y = self.year_combo.currentData()
        m = self.month_combo.currentData()
        insp = self.inspector_combo.currentData()

        where = []
        params = []
        if y:
            where.append("YEAR(h.created_at) = %s")
            params.append(int(y))
        if m:
            where.append("MONTH(h.created_at) = %s")
            params.append(int(m))
        if insp:
            where.append("h.check_by = %s")
            params.append(str(insp))

        where_sql = (" WHERE " + " AND ".join(where)) if where else ""

        # build months list only once (after year changed)
        if self.month_combo.count() <= 1:
            for i in range(1, 13):
                self.month_combo.addItem(f"{i:02d}", i)

        if not DB_ENABLED:
            self.count_lbl.setText("Showing 0 of 0 records (DB disabled)")
            self.page_lbl.setText("Page 1 of 1")
            self.prev_btn.setEnabled(False)
            self.next_btn.setEnabled(False)
            return

        try:
            conn = mysql.connector.connect(
                host=DB_HOST,
                port=DB_PORT,
                user=DB_USER,
                password=DB_PASSWORD,
                database=DB_NAME,
                use_pure=True,
            )
            cur = conn.cursor()

            # total count
            cur.execute(f"SELECT COUNT(*) FROM {DB_QC_HEADER} h{where_sql}", tuple(params))
            self._total_records = int((cur.fetchone() or (0,))[0] or 0)

            # page data
            offset = (self._page - 1) * self._per_page
            cur.execute(
                f"""
                SELECT
                    h.created_at,
                    h.check_by,
                    h.carton_no,
                    h.overall_result,
                    h.qc_no,
                    h.total_failed
                FROM {DB_QC_HEADER} h
                {where_sql}
                ORDER BY h.created_at DESC
                LIMIT %s OFFSET %s
                """,
                tuple(params + [self._per_page, offset]),
            )
            rows = cur.fetchall() or []
            cur.close()
            conn.close()

            # UI fill
            self.table.setRowCount(len(rows))
            for r, row in enumerate(rows):
                created_at, check_by, carton_no, overall_result, qc_no, total_failed = row

                dt_txt = str(created_at) if created_at is not None else "-"
                self.table.setItem(r, 0, QTableWidgetItem(dt_txt))
                self.table.setItem(r, 1, QTableWidgetItem((check_by or "-")))
                self.table.setItem(r, 2, QTableWidgetItem((carton_no or "-")))

                # status pill
                st = (overall_result or "").upper()
                status_lbl = QLabel("Pass" if st == "PASS" else ("Fail" if st == "FAIL" else (overall_result or "-")))
                status_lbl.setAlignment(Qt.AlignCenter)
                if st == "PASS":
                    status_lbl.setObjectName("HistPassPill")
                elif st == "FAIL":
                    status_lbl.setObjectName("HistFailPill")
                else:
                    status_lbl.setObjectName("HistNeutralPill")
                status_lbl.setAttribute(Qt.WA_StyledBackground, True)
                # ✅ paint table cell background (so you don't see page gradient behind the pill)
                try:
                    bg_item = QTableWidgetItem("")
                    bg_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                    bg_item.setBackground(QColor("#FFFFFF"))
                    self.table.setItem(r, 3, bg_item)
                except Exception:
                    pass
                self.table.setCellWidget(r, 3, status_lbl)

                rec_txt = self._recovery_text(str(carton_no or ""), str(qc_no or ""), int(total_failed or 0))
                rec_lbl = QLabel(rec_txt)
                rec_lbl.setAlignment(Qt.AlignCenter)
                if rec_txt == "Recovered":
                    rec_lbl.setObjectName("HistRecoveredPill")
                elif rec_txt == "Pending":
                    rec_lbl.setObjectName("HistPendingPill")
                else:
                    rec_lbl.setObjectName("HistNeutralPill")
                rec_lbl.setAttribute(Qt.WA_StyledBackground, True)
                # ✅ paint table cell background (so you don't see page gradient behind the pill)
                try:
                    bg_item2 = QTableWidgetItem("")
                    bg_item2.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                    bg_item2.setBackground(QColor("#FFFFFF"))
                    self.table.setItem(r, 4, bg_item2)
                except Exception:
                    pass
                self.table.setCellWidget(r, 4, rec_lbl)

                # details
                btn = QPushButton("View")
                btn.setObjectName("HistViewButton")
                btn.setCursor(Qt.PointingHandCursor)
                btn.clicked.connect(lambda _, c=str(carton_no or ""), q=str(qc_no or ""): self._open_details(c, q))
                self.table.setCellWidget(r, 5, btn)

            total_pages = max(1, (self._total_records + self._per_page - 1) // self._per_page)
            shown_to = min(self._total_records, self._page * self._per_page)
            self.count_lbl.setText(f"Showing {len(rows)} of {self._total_records} records")
            self.page_lbl.setText(f"Page {self._page} of {total_pages}")
            self.prev_btn.setEnabled(self._page > 1)
            self.next_btn.setEnabled(self._page < total_pages)

        except Exception as e:
            print("[ERROR] load_records:", e)
            self.count_lbl.setText("Showing 0 of 0 records")
            self.page_lbl.setText("Page 1 of 1")
            self.prev_btn.setEnabled(False)
            self.next_btn.setEnabled(False)

    def _open_details(self, carton_no: str, qc_no: str):
        # Uses existing BagDetailsDialog if present
        if not carton_no or not qc_no:
            QMessageBox.information(self, "Details", "No details available for this row.")
            return
        try:
            root = self.window()
            effect = QGraphicsOpacityEffect(root)
            effect.setOpacity(0.25)
            root.setGraphicsEffect(effect)
            try:
                dlg = BagDetailsDialog(parent=self, carton_no=carton_no, qc_no=qc_no)
                dlg.exec()
            finally:
                root.setGraphicsEffect(None)
        except Exception as e:
            QMessageBox.warning(self, "Details", f"Unable to open details: {e}")


class MainDashboardWindow(QMainWindow):
    """
    Top-level window with navigation bar.
    Page 0 = Home dashboard
    Page 1 = QC Carton Box Inspection (QCFormWindow as a page)
    """
    def __init__(self, current_user: str = ""):
        super().__init__()
        self.current_user = (current_user or "").strip()
        self.setWindowTitle("QC Inspection System")
        self.resize(1280, 800)

        self.setWindowFlags(Qt.FramelessWindowHint)

        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ---------- Top navigation bar ----------
        nav_bar = QFrame()
        nav_bar.setObjectName("TopNav")
        nav_layout = QHBoxLayout(nav_bar)
        nav_layout.setContentsMargins(24, 10, 24, 10)
        nav_layout.setSpacing(24)

        title_label = QLabel("QC Inspection System")
        title_label.setObjectName("NavTitle")

        nav_layout.addWidget(title_label)
        nav_layout.addStretch()

        def make_nav_btn(text):
            btn = QPushButton(text)
            btn.setObjectName("NavButton")
            btn.setFlat(True)
            btn.setCursor(Qt.PointingHandCursor)
            return btn

        self.home_btn = make_nav_btn("Home")
        self.start_btn = make_nav_btn("Start Inspection")
        self.reporting_btn = make_nav_btn("Reporting")
        self.config_btn = make_nav_btn("Dashboard")
        self.history_btn = make_nav_btn("Inspection History")

        nav_layout.addWidget(self.home_btn)
        nav_layout.addWidget(self.start_btn)
        nav_layout.addWidget(self.reporting_btn)
        nav_layout.addWidget(self.config_btn)
        nav_layout.addWidget(self.history_btn)
        
        nav_layout.addStretch()
        self.logout_btn = make_nav_btn("Logout")
        self.logout_btn.setObjectName("LogoutButton")
        self.logout_btn.clicked.connect(self.logout)

        nav_layout.addWidget(self.logout_btn)


        main_layout.addWidget(nav_bar)

        # ---------- Stacked pages ----------
        self.stack = QStackedWidget()
        main_layout.addWidget(self.stack)

        # ===== Page 0: Home dashboard =====
        home_page = QWidget()
        home_layout = QVBoxLayout(home_page)
        home_layout.setContentsMargins(80, 40, 80, 40)
        home_layout.setSpacing(32)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        scroll.setWidget(body)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(24)

        hero_card = QFrame()
        hero_card.setObjectName("HeroCard")
        hero_layout = QVBoxLayout(hero_card)
        hero_layout.setContentsMargins(40, 32, 40, 32)
        hero_layout.setSpacing(16)

        hero_title = QLabel("""
        <span style='font-size:32px; font-weight:800; color:#1e293b;'>
        Welcome to QC Inspection System
        </span>
        """)
        hero_title.setObjectName("HeroTitle")
        hero_title.setAlignment(Qt.AlignCenter)

        hero_sub = QLabel("""
        <span style='font-size:16px; color:#475569;'>
        Streamline your quality control process with our comprehensive carton box and bag
        inspection system. Monitor weight, metal detection, cleanliness, and more with real-time
        digital scale integration.
        </span>
        """)
        hero_sub.setObjectName("HeroSubtitle")
        hero_sub.setWordWrap(True)
        hero_sub.setAlignment(Qt.AlignCenter)


        hero_layout.addWidget(hero_title)
        hero_layout.addWidget(hero_sub)
        body_layout.addWidget(hero_card)

        # ===== Overall Production Status big card =====
        overall_card = QFrame()
        overall_card.setObjectName("OverallStatsCard")
        overall_card.setAttribute(Qt.WA_StyledBackground, True)
        overall_layout = QVBoxLayout(overall_card)
        overall_layout.setContentsMargins(32, 24, 32, 24)
        overall_layout.setSpacing(16)

        # dictionary: keep references to labels so we can update from MySQL
        self.home_stats_labels = {}


        # Top row: title + "Live Updates"
        top_row = QHBoxLayout()
        stats_title = QLabel("Production Status Today")
        stats_title.setObjectName("StatsCardTitle")
        top_row.addWidget(stats_title)

        top_row.addStretch()

        live_lbl = QLabel("● Live Updates")
        live_lbl.setObjectName("LiveStatusLabel")
        top_row.addWidget(live_lbl)

        overall_layout.addLayout(top_row)

        # Row with 4 mini-cards
        stats_row = QHBoxLayout()
        stats_row.setSpacing(16)

        def make_stat_card(main_text, title, subtitle, obj_name,
                           main_key=None, sub_key=None):
            card = QFrame()
            card.setObjectName(obj_name)
            card.setAttribute(Qt.WA_StyledBackground, True)

            cl = QVBoxLayout(card)
            cl.setContentsMargins(20, 16, 20, 16)
            cl.setSpacing(6)

            main_lbl = QLabel(main_text)
            main_lbl.setObjectName("StatMainNumber")

            title_lbl = QLabel(title)
            title_lbl.setObjectName("StatTitle")

            sub_lbl = QLabel(subtitle)
            sub_lbl.setObjectName("StatSubtitle")
            sub_lbl.setWordWrap(True)

            cl.addWidget(main_lbl)
            cl.addWidget(title_lbl)
            cl.addWidget(sub_lbl)

            # store references so refresh_overall_home_stats() can update
            if main_key is not None:
                self.home_stats_labels[main_key] = main_lbl
            if sub_key is not None:
                self.home_stats_labels[sub_key] = sub_lbl

            return card


        # start with 0, we'll update from MySQL
        pass_rate_card = make_stat_card(
            "✅ 0.0%",
            "Overall Pass Rate",
            "Based on 0 inspections today",
            "StatPassRateCard",
            main_key="pass_rate_main",
            sub_key="pass_rate_sub",
        )
        total_card = make_stat_card(
            "📅 0",
            "Total Inspections",
            "Completed today",
            "StatTotalCard",
            main_key="total_main",
        )
        passed_card = make_stat_card(
            "👍 0",
            "Passed",
            "Within specifications",
            "StatPassedCard",
            main_key="passed_main",
        )
        failed_card = make_stat_card(
            "❌ 0",
            "Failed",
            "Requires attention",
            "StatFailedCard",
            main_key="failed_main",
        )
        recovered_card = make_stat_card(
            "♻ 0",
            "Recovered",
            "Bags recovered today",
            "StatRecoveredCard",
            main_key="recovered_main",
        )

        # add all 5 mini-cards to the row
        stats_row.addWidget(pass_rate_card)
        stats_row.addWidget(total_card)
        stats_row.addWidget(passed_card)
        stats_row.addWidget(failed_card)
        stats_row.addWidget(recovered_card)

        overall_layout.addLayout(stats_row)

        body_layout.addWidget(overall_card)
        # ===== End Overall Production Status card =====

        cards_row = QHBoxLayout()
        cards_row.setSpacing(24)


        def make_card(title, text, button_text=None, primary=False, callback=None):
            card = QFrame()
            card.setObjectName("HomeCard")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(24, 24, 24, 24)
            cl.setSpacing(12)

            # -------- IMAGE FOR HOME CARDS (SAME STYLE) --------
            image_map = {
                "Start New Inspection": "QC.jpg",
                "Failed Cartons": "FAILED.jpg",      # <-- your image
                "Dashboard": "DASHBOARD.jpg",        # <-- your image
            }

            if title in image_map:
                img_label = QLabel()
                img_label.setObjectName("HomeCardImage")
                img_label.setFixedSize(520, 300)     # 🔥 SAME SIZE FOR ALL
                img_label.setAlignment(Qt.AlignCenter)

                pix = QPixmap(image_map[title])
                if not pix.isNull():
                    pix = pix.scaled(
                        img_label.width(),
                        img_label.height(),
                        Qt.KeepAspectRatioByExpanding,
                        Qt.SmoothTransformation
                    )
                    img_label.setPixmap(pix)

                cl.addWidget(img_label)

            # ------------------------------------------------------

            t = QLabel(title)
            t.setObjectName("CardTitle")
            desc = QLabel(text)
            desc.setObjectName("CardSubtitle")
            desc.setWordWrap(True)

            cl.addWidget(t)
            cl.addWidget(desc)
            cl.addStretch()

            if button_text:
                btn = QPushButton(button_text)
                btn.setObjectName("PrimaryCardButton" if primary else "SecondaryCardButton")
                btn.setCursor(Qt.PointingHandCursor)
                if callback:
                    btn.clicked.connect(callback)
                cl.addWidget(btn, 0, Qt.AlignLeft)

            return card

        start_card = make_card(
            "Start New Inspection",
            "Begin QC for a new carton.",
            "Start Inspection",
            True,
            self.show_qc_form,
        )
        reports_card = make_card(
            "Failed Cartons",
            "See all cartons that failed QC and their details.",
            "View Failed Cartons",
            False,
            self.show_reporting,   # still opens the reporting page with the failed cards
        )

        config_card = make_card(
            "Dashboard",
            "Inspection Performance Dashboard.",
            "View Dashboard",
            False,
            self.show_config,
        )

        cards_row.addWidget(start_card)
        cards_row.addWidget(reports_card)
        cards_row.addWidget(config_card)
        body_layout.addLayout(cards_row)

        home_layout.addWidget(scroll)
        self.stack.addWidget(home_page)   # index 0

        # ===== Page 1: QCFormWindow =====
        self.qc_page = QCFormWindow()
        # --- Autofill Checked By from login username ---
        if self.current_user and hasattr(self.qc_page, "checked_by_edit"):
            self.qc_page.checked_by_edit.setText(self.current_user)
            self.qc_page.checked_by_edit.setReadOnly(True)  # optional lock
            
        qc_container = QWidget()
        qc_layout = QVBoxLayout(qc_container)
        qc_layout.setContentsMargins(0, 0, 0, 0)
        qc_layout.setSpacing(0)
        qc_layout.addWidget(self.qc_page)
        self.stack.addWidget(qc_container)  # index 1

        # ===== Page 2: Reporting (Failed Cartons Overview) =====
        self.reporting_page = FailedCartonsPage(
            parent=self,
            open_new_inspection_callback=self.show_qc_form,  # button "Add New Failed Carton"
        )
        self.stack.addWidget(self.reporting_page)   # index 2


        

        # ===== Page 3: Inspection Performance Dashboard =====
        self.config_page = PerformanceDashboardPage(parent=self)
        self.stack.addWidget(self.config_page)   # index 3

        # ===== Page 4: Inspection History =====
        self.history_page = InspectionHistoryPage(parent=self)
        self.stack.addWidget(self.history_page)  # index 4



        # Nav actions
        self.home_btn.clicked.connect(self.show_home)
        self.start_btn.clicked.connect(self.show_qc_form)
        self.reporting_btn.clicked.connect(self.show_reporting)
        self.config_btn.clicked.connect(self.show_config)
        self.history_btn.clicked.connect(self.show_history) 

        self._apply_nav_styles()

    def show_history(self):
        try:
            if hasattr(self, 'config_page') and hasattr(self.config_page, 'stop_monitoring'):
                self.config_page.stop_monitoring()
        except Exception:
            pass
        self.stack.setCurrentIndex(4)
        try:
            if hasattr(self, 'history_page') and hasattr(self.history_page, 'reload_filter_options'):
                self.history_page.reload_filter_options()
            if hasattr(self, 'history_page') and hasattr(self.history_page, 'load_records'):
                self.history_page.load_records(page=1)
        except Exception:
            pass


    def show_home(self):
        try:
            if hasattr(self, 'config_page') and hasattr(self.config_page, 'stop_monitoring'):
                self.config_page.stop_monitoring()
        except Exception:
            pass
        self.refresh_overall_home_stats()
        self.stack.setCurrentIndex(0)


    def show_qc_form(self):
        try:
            if hasattr(self, 'config_page') and hasattr(self.config_page, 'stop_monitoring'):
                self.config_page.stop_monitoring()
        except Exception:
            pass
        self.stack.setCurrentIndex(1)

    def show_reporting(self):
        """Open Reporting page and refresh failed-carton cards."""
        try:
            if hasattr(self, 'config_page') and hasattr(self.config_page, 'stop_monitoring'):
                self.config_page.stop_monitoring()
        except Exception:
            pass
        self.refresh_failed_cartons()
        self.stack.setCurrentIndex(2)

    def show_coming_soon(self):
        QMessageBox.information(
            self,
            "Coming Soon",
            "This section is not implemented yet.\nCurrently only Start Inspection and Reporting are active.",
        )


    def show_config(self):
        """Open Inspection Performance Dashboard (Configuration)."""
        try:
            if hasattr(self, "config_page") and hasattr(self.config_page, "start_monitoring"):
                self.config_page.start_monitoring()
        except Exception:
            pass
        self.stack.setCurrentIndex(3)

    def show_reporting(self):
        """Open Reporting page and refresh failed-carton cards."""
        try:
            if hasattr(self, 'config_page') and hasattr(self.config_page, 'stop_monitoring'):
                self.config_page.stop_monitoring()
        except Exception:
            pass
        self.reporting_page.refresh_failed_cartons()
        self.stack.setCurrentIndex(2)

    def logout(self):
        reply = QMessageBox.question(
            self,
            "Logout",
            "Are you sure you want to logout?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            self.login_window = LoginWindow()
            self.login_window.show()
            self.close()



    def _apply_nav_styles(self):
        base = """
        QMainWindow, QWidget {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                        stop:0 #EFF6FF,
                                        stop:1 #ECFDF5);
            color: #111827;
            font-family: "Segoe UI";
        }

        
        QLabel, QGroupBox::title {
            background: transparent;
        }
        #HeroCard QLabel,
        #HomeCard QLabel {
            background-color: #FFFFFF;
        }
        """


        nav = """
        #TopNav {
            background-color: #FFFFFF;    /* PURE WHITE */
            border-bottom: 1px solid #E5E7EB;
        }
        #FailedCartonPill {
            background-color: #FEE2E2;
            color: #B91C1C;
            padding: 4px 12px;
            border-radius: 8px;
            font-size: 12pt;
            font-weight: 600;
        }
        /* Yellow pill → Pending Recovery */
        #PendingRecoveryPill {
            background-color: #FEF3C7;     /* light yellow */
            color: #92400E;                /* dark amber text */
            padding: 4px 10px;
            border-radius: 999px;
            font-size: 11px;
            font-weight: 600;
        }

        /* Green pill → all failed bags recovered */
        #RecoveredPill {
            background-color: #DCFCE7;     /* light green */
            color: #166534;                /* dark green text */
            padding: 4px 10px;
            border-radius: 999px;
            font-size: 11px;
            font-weight: 600;
        }

        #TopHeader {
            background-color: #FFFFFF;
            border: none;
        }

        #NavTitle {
            font-size: 12pt;
            font-weight: 700;
            color: #111827;
        }

        #NavButton {
            background: transparent;
            border: none;
            color: #4B5563;
            padding: 6px 12px;
            font-size: 10.5pt;
        }
        #NavButton:hover {
            color: #111827;
        }

        #HeroCard, #HomeCard {
            background-color: #FFFFFF;
            border-radius: 12px;
            border: 1px solid #E5E7EB;
        }

        #CardTitle {
            font-size: 13pt;
            font-weight: 600;
            color: #111827;
            background-color: #FFFFFF;
        }

        #CardSubtitle {
            font-size: 10.5pt;
            color: #4B5563;
            background-color: #FFFFFF;
        }

        #PrimaryCardButton {
            background-color: #2563EB;
            color: white;
            border-radius: 6px;
            padding: 8px 16px;
            font-weight: 600;
        }
        #PrimaryCardButton:hover {
            background-color: #1D4ED8;
        }

        #SecondaryCardButton {
            background-color: #E5E7EB;
            color: #111827;
            border-radius: 6px;
            padding: 8px 16px;
            font-weight: 600;
        }
        #SecondaryCardButton:hover {
            background-color: #D1D5DB;
        }
        #LogoutButton {
            background-color: #EF4444;
            color: white;
            font-weight: 600;
            border-radius: 6px;
            padding: 8px 16px;
        }

        #LogoutButton:hover {
            background-color: #DC2626;
        }

        

/* -------- Inspection History -------- */
#InspectionHistoryPage {
    background: transparent;
}
QLabel#HistTitle {
    font-size: 20pt;
    font-weight: 800;
    color: #111827;
}
QLabel#HistSubtitle {
    font-size: 10.5pt;
    color: #6B7280;
}
QFrame#HistCard {
    background-color: #FFFFFF;
    border: 1px solid #E5E7EB;
    border-radius: 16px;
}
QLabel#HistCardTitle {
    font-size: 12pt;
    font-weight: 800;
    color: #111827;
}
QLabel#HistSmallText {
    font-size: 9.5pt;
    color: #6B7280;
}
QPushButton#HistLinkButton {
    background: transparent;
    border: none;
    color: #2563EB;
    font-weight: 600;
    padding: 6px 8px;
}
QPushButton#HistLinkButton:hover {
    text-decoration: underline;
}
QFrame#HistFieldBox {
    background-color: #F9FAFB;
    border: 1px solid #E5E7EB;
    border-radius: 12px;
}
QLabel#HistFieldLabel {
    font-size: 9.5pt;
    font-weight: 700;
    color: #111827;
}
QLabel#HistFieldIcon {
    font-size: 11pt;
}
QComboBox#HistCombo, QComboBox#HistComboSmall {
    background-color: #FFFFFF;
    border: 1px solid #D1D5DB;
    border-radius: 10px;
    padding: 8px 10px;
    min-height: 38px;
    font-size: 10pt;
}
QComboBox#HistComboSmall {
    min-height: 34px;
    padding: 6px 10px;
}
QComboBox#HistCombo:focus, QComboBox#HistComboSmall:focus {
    border: 2px solid #2563EB;
}
QPushButton#HistPrimaryButton {
    background-color: #1D4ED8;
    color: #FFFFFF;
    font-weight: 800;
    border: none;
    border-radius: 12px;
    padding: 10px 18px;
    min-height: 44px;
}
QPushButton#HistPrimaryButton:hover { background-color: #1E40AF; }
QPushButton#HistGhostButton {
    background-color: #FFFFFF;
    border: 1px solid #D1D5DB;
    border-radius: 10px;
    padding: 8px 14px;
    font-weight: 700;
    color: #111827;
    min-height: 38px;
}
QPushButton#HistGhostButton:hover { background-color: #F3F4F6; }
QPushButton#HistViewButton {
    background-color: #F3F4F6;
    border: 1px solid #E5E7EB;
    border-radius: 10px;
    padding: 6px 12px;
    font-weight: 700;
    color: #111827;
}
QPushButton#HistViewButton:hover { background-color: #E5E7EB; }

QLabel#HistPassPill {
    background-color: #DCFCE7;
    color: #166534;
    padding: 4px 12px;
    border-radius: 999px;
    font-weight: 800;
    min-width: 60px;
}
/* Fix green/colored background on "Showing ..." and "Page ..." */QLabel#HistSmallText {
    background: transparent;
    background-color: transparent;
    color: #6B7280;
    font-weight: 700;
}

QLabel#HistFailPill {
    background-color: #FEE2E2;
    color: #B91C1C;
    padding: 4px 12px;
    border-radius: 999px;
    font-weight: 800;
    min-width: 60px;
}
QLabel#HistPendingPill {
    background-color: #FEF3C7;
    color: #92400E;
    padding: 4px 12px;
    border-radius: 999px;
    font-weight: 800;
    min-width: 80px;
}
QLabel#HistRecoveredPill {
    background-color: #DCFCE7;
    color: #166534;
    padding: 4px 12px;
    border-radius: 999px;
    font-weight: 800;
    min-width: 90px;
}

            /* Recovered pill (after edit + reweigh) */
            #RecoveredPill {
                background-color: #DCFCE7;
                color: #166534;
                padding: 4px 14px;
                border-radius: 999px;
                font-weight: 700;
                min-width: 86px;
            }

            /* Disabled recovered action button */
            #RecoverButton {
                background-color: #16A34A;
                color: #FFFFFF;
                border-radius: 8px;
                padding: 6px 14px;
                font-weight: 700;
                border: none;
            }
            #RecoverButton:disabled {
                background-color: #16A34A;
                color: #FFFFFF;
                opacity: 0.85;
            }

QLabel#HistNeutralPill {
    background-color: #E5E7EB;
    color: #374151;
    padding: 4px 12px;
    border-radius: 999px;
    font-weight: 800;
    min-width: 60px;
}

QTableWidget#HistTable {
    background-color: #FFFFFF;
    border: 1px solid #E5E7EB;
    border-radius: 12px;
    gridline-color: transparent;
    selection-background-color: rgba(37, 99, 235, 0.12);
    selection-color: #111827;
}
QHeaderView::section {
    background-color: #F9FAFB;
    color: #6B7280;
    border: none;
    padding: 10px 10px;
    font-weight: 800;
    font-size: 9.5pt;
}
QTableWidget::item {
    padding: 10px;
    border: none;
    color: #111827;
}
"""
        reports = """

        /* Whole reporting page background → green gradient */
        #ReportingPage {
            background: qlineargradient(
                x1:0, y1:0, x2:0, y2:1,
                stop:0 #EFF6FF,   /* light blue (top) */
                stop:1 #ECFDF5    /* light green (bottom) */
            );
        }

        /* Big white card that holds title + all failed-carton cards */
        #ReportingPanel {
            background-color: #FFFFFF;
            border-radius: 16px;
            border: 1px solid #E5E7EB;
        }

        /* Container inside scroll – also white so it blends with panel */
        #FailedCartonContainer {
            background-color: #FFFFFF;
        }

       /* Individual carton cards */
        #FailedCartonCard {
            background: #FFFFFF;
            border-radius: 12px;
            border: 1px solid #E5E7EB;
        }

        #FailedCartonTitle {
            background-color: #FFFFFF;
            font-size: 13pt;
            font-weight: 600;
            color: #111827;
        }

        #FailedCartonSubtitle {
            font-size: 9.5pt;
            background-color: #FFFFFF;
            color: #6B7280;
        }

        #FailedCountLabel {
            background-color: #FEE2E2;      /* soft red background */
            color: #DC2626;                 /* red text */
            padding: 4px 10px;
            border-radius: 6px;
            font-weight: 600;
            font-size: 12pt;
        }
        /* Force icon backgrounds & helper text to be white */
        #IconLabel {
            background-color: #FFFFFF;
            font-size: 15pt;      /* 🔹 make icon bigger */
            padding-right: 6px;   /* small space between icon and text */

        }

        #HelperText {
            background-color: #FFFFFF;
            color: #6B7280;
        }

        #FailedCartonRowLabel {
            color: #4B5563;
            background-color: #FFFFFF;
            font-size: 12pt;
        }

        #FailedCartonRowValue {
            color: #111827;
            background-color: #FFFFFF;
            font-weight: 600;
            font-size: 12pt;
        }

        #FailedCartonRemarksBox {
            background-color: #F9FAFB;
            border-radius: 6px;
            border: 1px solid #E5E7EB;
            padding: 6px 10px;
            font-size: 9.5pt;
            color: #111827;
        }

        #FailedCartonHint {
            background-color: #FFFFFF;      /* Make background clean white */
            color: #6B7280;                 /* Medium grey */
            font-size: 12pt;                /* Bigger text */
            font-weight: 600;               /* Semi-bold */
            font-style: italic;             /* Keep the style */
            padding: 4px 0;                 /* Add some spacing */
        }

        /* Title bar for reporting page */
        #FailedCartonsTitle {
            background-color: #FFFFFF;
            padding: 8px 12px;
            font-weight: 700;
            font-size: 25pt;
            color: #1E293B;
            border-radius: 4px;
        }
        /* Red FAILED pill */
        #FailedCartonPill {
            background-color: #FEE2E2;   /* soft red */
            color: #DC2626;              /* strong red text */
            padding: 4px 12px;
            font-weight: 600;
            border-radius: 6px;
            font-size: 11pt;
        }
                /* ===== Custom vertical scrollbar (same as Start Inspection) ===== */
        QScrollArea {
            border: none;
        }

        QScrollBar:vertical {
            background: #E5E7EB;      /* light grey track */
            width: 14px;
            margin: 0px;              /* no gap at top/bottom */
            border-radius: 7px;
        }

        QScrollBar::handle:vertical {
            background: #3B82F6;      /* blue thumb */
            min-height: 48px;
            border-radius: 7px;
        }

        QScrollBar::handle:vertical:hover {
            background: #2563EB;      /* darker blue on hover */
        }

        /* Remove arrow-button area so track is full height */
        QScrollBar::sub-line:vertical,
        QScrollBar::add-line:vertical {
            height: 0px;
            margin: 0px;
            border: none;
            background: transparent;
        }

        /* Hide arrows completely */
        QScrollBar::up-arrow:vertical,
        QScrollBar::down-arrow:vertical {
            width: 0px;
            height: 0px;
            border: none;
            background: transparent;
        }

        /* Pages above/below thumb */
        QScrollBar::add-page:vertical,
        QScrollBar::sub-page:vertical {
            background: transparent;
        }
        #FailedCartonCTN {
            font-size: 20pt;
            font-weight: 800;
            color: #111827;
            background-color: #FFFFFF;
        }
        #PendingRecoveryPill {
            background-color: #FEF3C7;   /* light yellow */
            color: #92400E;              /* dark amber */
            padding: 4px 10px;
            border-radius: 999px;
            font-size: 11px;
            font-weight: 600;
        }

        #RecoveredPill {
            background-color: #DCFCE7;   /* light green */
            color: #166534;              /* dark green */
            padding: 4px 10px;
            border-radius: 999px;
            font-size: 11px;
            font-weight: 600;
        }
                /* ===== Home – Overall Production Status card ===== */
        #OverallStatsCard {
            background-color: #FFFFFF;
            border-radius: 18px;
            border: 1px solid #E5E7EB;
        }

        #StatsCardTitle {
            font-size: 18pt;
            font-weight: 700;
            color: #111827;
        }

        #LiveStatusLabel {
            font-size: 10pt;
            font-weight: 600;
            color: #16A34A;            /* green */
        }

        #StatMainNumber {
            font-size: 20pt;
            font-weight: 800;
        }

        #StatTitle {
            font-size: 11pt;
            font-weight: 700;
            color: #111827;
        }

        #StatSubtitle {
            font-size: 9pt;
            color: #4B5563;
        }

        /* individual mini-card backgrounds */
        #StatPassRateCard {
            background-color: #ECFDF3;    /* light green */
            border-radius: 18px;
        }
        #StatTotalCard {
            background-color: #EFF6FF;    /* light blue */
            border-radius: 18px;
        }
        #StatPassedCard {
            background-color: #DCFCE7;    /* mint */
            border-radius: 18px;
        }
        #StatFailedCard {
            background-color: #FEE2E2;    /* light red */
            border-radius: 18px;
        }
        #StatRecoveredCard {
            background-color: #E0F2FE;   /* soft blue/green */
            border-radius: 18px;
        }
        #StatRecoveredCard {
            background-color: #E0F2FE;
            border-radius: 18px;
        }
        /* Fix: labels inside Overall Production cards should not be white */
        #OverallStatsCard QLabel,
        #StatPassRateCard QLabel,
        #StatTotalCard QLabel,
        #StatPassedCard QLabel,
        #StatFailedCard QLabel,
        #StatRecoveredCard QLabel {
            background-color: transparent;
        }

        """

        
        perf = """
        /* ===== Inspection Performance Dashboard (dark) ===== */
        #PerfDashboardPage {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                        stop:0 #0B1220,
                                        stop:1 #0A1020);
            color: #E5E7EB;
        }
        #PerfTitle {
            font-size: 22pt;
            font-weight: 800;
            color:  #000000;
        }
        #PerfSubtitle {
            color: #6B7280;          /* GREY */
            font-size: 13pt;
        }
        #PerfRangeCombo {
            background-color: #1F2937;   /* dark gray */
            border: 1px solid #374151;
            border-radius: 10px;
            padding: 6px 14px;
            color: #FFFFFF;              /* white text */
            min-width: 160px;
            min-height: 36px;
            font-size: 11pt;
        }
        /* Dropdown popup list */
        #PerfRangeCombo QAbstractItemView {
            background-color: #FFFFFF;    /* white list */
            color: #111827;               /* black text */
            border: 1px solid #D1D5DB;
            selection-background-color: #2563EB; /* blue highlight */
            selection-color: #FFFFFF;
            outline: 0;
        }


        /* remove default arrow border */
        #PerfRangeCombo::drop-down {
            border: none;
        }

        #PerfFilterButton {
            background-color: #1F2937;   /* dark gray */
            border: 1px solid #374151;
            border-radius: 10px;
            padding: 8px 18px;
            color: #FFFFFF;
            font-size: 11pt;
            font-weight: 700;
        }

        #PerfFilterButton:hover {
            background-color: #111827;   /* darker on hover */
        }

        #PerfExportButton:hover { opacity: 0.95; }

        #PerfCard {
            background: rgba(255,255,255,0.06);
            border: 1px solid rgba(255,255,255,0.10);
            border-radius: 16px;
        }
        #PerfCardHeader {
            font-size: 9.5pt;
            font-weight: 700;
            color: rgba(255,255,255,0.70);
        }
        #PerfCardIcon {
            background: rgba(255,255,255,0.08);
            border-radius: 10px;
            padding: 8px;
            min-width: 28px;
            min-height: 28px;
            qproperty-alignment: 'AlignCenter';
            font-size: 14pt;
        }
        #PerfMainValue {
            font-size: 26pt;
            font-weight: 900;
            color: #FFFFFF;
        }
        #PerfSubValue {
            font-size: 10pt;
            color: rgba(255,255,255,0.72);
        }
        #PerfMainValueFail {
            font-size: 22pt;
            font-weight: 900;
            color: #F87171;
        }
        #PerfMainValueRecover {
            font-size: 22pt;
            font-weight: 900;
            color: #FBBF24;
        }
        #PerfMiniCaption {
            font-size: 9.5pt;
            color: rgba(255,255,255,0.65);
        }
        #PerfRecoveryRate {
            font-size: 10pt;
            color: #FBBF24;
            font-weight: 800;
        }
        #PerfProgressBar {
            background: rgba(255,255,255,0.10);
            border-radius: 999px;
        }
        #PerfFooter {
            font-size: 9.5pt;
            color: rgba(255,255,255,0.60);
        }
        #PerfCard {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 #2B2F36,
                stop:1 #1F232A
            );
            border: 1px solid rgba(255,255,255,0.10);
            border-radius: 16px;
        }

        #PerfCardTitle {
            color: rgba(255,255,255,0.70);
            font-size: 12px;
            font-weight: 700;
            letter-spacing: 1px;
        }

        #PerfCardValue {
            color: white;
            font-size: 34px;
            font-weight: 800;
        }

        #PerfCardIcon {
            min-width: 46px;
            max-width: 46px;
            min-height: 46px;
            max-height: 46px;
            background: rgba(59,130,246,0.25);
            border: 1px solid rgba(255,255,255,0.10);
            border-radius: 12px;
            color: white;
            font-size: 20px;
        }

        #PerfCardFootMuted {
            color: rgba(255,255,255,0.55);
            font-size: 12px;
            font-weight: 600;
        }

        #PerfCardFootGood {
            color: #34D399;
            font-size: 12px;
            font-weight: 700;
        }
        /* === Force dark page background for Performance Dashboard === */
        #PerfDashboardPage {
            background: #0B1220;   /* dark page like your 2nd picture */
            color: #FFFFFF;
        }

        /* stop QLabel from painting its own background blocks */
        #PerfDashboardPage QLabel {
            background: transparent;
        }
        #PerfTopLabel {
            color: rgba(255,255,255,0.75);
            font-size: 12pt;
            font-weight: 600;
            background: transparent;
        }
        /* Right green icon tile */
        #PerfCardIcon {
            background: rgba(34,197,94,0.20);
            border: 1px solid rgba(34,197,94,0.35);
            border-radius: 12px;
            min-width: 54px;
            min-height: 54px;
            qproperty-alignment: 'AlignCenter';
            color: #22C55E;
            font-size: 18pt;
            font-weight: 900;
        }

        /* Hide the dummy sub label (we use improvement row instead) */
        #PerfSubValue {
            max-height: 0px;
            min-height: 0px;
            color: transparent;
        }

        /* Improvement row */
        #PerfImproveArrow {
            color: #22C55E;
            font-weight: 900;
            font-size: 12pt;
        }
        #PerfImprovePct {
            color: #22C55E;
            font-weight: 900;
            font-size: 11pt;
        }
        #PerfImproveText {
            color: rgba(255,255,255,0.65);
            font-weight: 700;
            font-size: 10pt;
        }

        /* Progress bar (green chunk + dark remainder) */
        QProgressBar#PerfProgress {
            background: rgba(255,255,255,0.12);
            border: 0px;
            border-radius: 999px;
            min-height: 10px;
            max-height: 10px;
        }
        QProgressBar#PerfProgress::chunk {
            background: #22C55E;
            border-radius: 999px;
        }
        /* ===== PASS RATE – Improvement row ===== */
        #PerfImproveRow {
            background: transparent;
        }

        /* Arrow */
        #PerfImproveArrow {
            color: #22C55E;
            font-weight: 900;
            font-size: 12pt;
        }

        /* Percentage */
        #PerfImprovePct {
            color: #22C55E;
            font-weight: 900;
            font-size: 11pt;
        }

        /* Text */
        #PerfImproveText {
            color: rgba(255,255,255,0.65);
            font-weight: 600;
            font-size: 10pt;
        }
        /* ===== PASS RATE improvement row FIX ===== */
        #PerfImproveRow {
            background: transparent;
        }

        #PerfImproveRow QLabel {
            background: transparent;
        }
        /* BIG up arrow for improvement */
        #PerfImproveArrow {
            font-size: 22pt;     /* 🔥 increase this if you want bigger */
            font-weight: 900;
            color: #22C55E;
            padding-right: 2px;
        }
        #PerfTopLabel {
            color: #000000;        /* BLACK */
            font-size: 12pt;
            font-weight: 600;
        }
        /* ===== AVG INSPECTION TIME – Trend row ===== */
        #PerfTrendRow { background: transparent; }
        #PerfTrendRow QLabel { background: transparent; }

        #PerfTrendArrow { color: #22C55E; font-weight: 900; font-size: 22pt; }
        #PerfTrendValue { color: #22C55E; font-weight: 900; font-size: 11pt; }
        #PerfTrendText  { color: rgba(255,255,255,0.65); font-weight: 600; font-size: 10pt; }

        #PerfFooterText { color: rgba(255,255,255,0.65); font-weight: 600; font-size: 10pt; }

        """

        self.setStyleSheet(base + nav + reports + perf)

    def refresh_overall_home_stats(self):
        """
        Read today's QC stats from MySQL and update the Overall Production Status
        card on the Home page.

        - Total / Pass / Fail from gama_qc_carton_header
        - Recovered = bags with actions = 'Recover' from gama_qc_carton_bag
        """
        if not hasattr(self, "home_stats_labels"):
            return

        total = passed = failed = recovered = 0

        if DB_ENABLED:
            try:
                conn = mysql.connector.connect(
                    host=DB_HOST,
                    port=DB_PORT,
                    user=DB_USER,
                    password=DB_PASSWORD,
                    database=DB_NAME,
                    use_pure=True,
                )
                cur = conn.cursor()

                # ---- header stats (per carton) ----
                cur.execute(
                    f"""
                    SELECT
                        SUM(CASE WHEN overall_result='PASS' THEN 1 ELSE 0 END) AS pass_cnt,
                        SUM(CASE WHEN overall_result='FAIL' THEN 1 ELSE 0 END) AS fail_cnt,
                        COUNT(*) AS total_cnt
                    FROM {DB_QC_HEADER}
                    WHERE DATE(created_at) = CURDATE()
                    """
                )
                row = cur.fetchone()
                if row:
                    passed = int(row[0] or 0)
                    failed = int(row[1] or 0)
                    total  = int(row[2] or 0)

                # ---- recovered bags (per bag) ----
                cur.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM {DB_QC_BAG}
                    WHERE DATE(created_at) = CURDATE()
                      AND LOWER(COALESCE(actions, '')) = 'recover'
                    """
                )
                row2 = cur.fetchone()
                if row2:
                    recovered = int(row2[0] or 0)

                cur.close()
                conn.close()
            except Exception as e:
                print("[WARN] refresh_overall_home_stats DB error:", e)

        # compute pass rate
        pass_rate = (passed / total * 100.0) if total > 0 else 0.0

        lbls = self.home_stats_labels

        if "pass_rate_main" in lbls:
            lbls["pass_rate_main"].setText(f"✅ {pass_rate:.1f}%")
        if "pass_rate_sub" in lbls:
            lbls["pass_rate_sub"].setText(f"Based on {total} inspections today")
        if "total_main" in lbls:
            lbls["total_main"].setText(f"📅 {total}")
        if "passed_main" in lbls:
            lbls["passed_main"].setText(f"👍 {passed}")
        if "failed_main" in lbls:
            lbls["failed_main"].setText(f"❌ {failed}")
        if "recovered_main" in lbls:
            lbls["recovered_main"].setText(f"♻ {recovered}")



# ---------------- Login Window ---------------- #

class AdminLoginDialog(QDialog):
    """Small admin gate before allowing Sign up."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Admin Login Access")
        self.setModal(True)
        self.setFixedWidth(360)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(10)

        title = QLabel("Admin Login Access")
        title.setStyleSheet("font-size: 12pt; font-weight: 700;")
        root.addWidget(title)

        root.addWidget(QLabel("User :"))
        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("Enter admin user")
        root.addWidget(self.user_edit)

        root.addWidget(QLabel("Password:"))
        self.pass_edit = QLineEdit()
        self.pass_edit.setPlaceholderText("Enter admin password")
        self.pass_edit.setEchoMode(QLineEdit.Password)
        root.addWidget(self.pass_edit)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)

        ok_btn = QPushButton("Login")
        ok_btn.setDefault(True)     # 🔥 ENTER key triggers Login
        ok_btn.clicked.connect(self._check_login)

        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        root.addLayout(btn_row)

        self.user_edit.setFocus()

    def _check_login(self):
        u = self.user_edit.text().strip()
        p = self.pass_edit.text().strip()

        if u == ADMIN_USER and p == ADMIN_PASS:
            self.accept()
        else:
            QMessageBox.warning(self, "Access Denied", "Invalid admin username or password.")
            self.pass_edit.clear()
            self.pass_edit.setFocus()


class LoginWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("GAMALITE SDN BHD - QC Login")
        self.resize(600, 720)
        self.showMaximized()
        central = QWidget()
        self.setCentralWidget(central)

        self._apply_styles()

        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 32)
        root_layout.setSpacing(0)

        # Center everything vertically
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setAlignment(Qt.AlignCenter)
        container_layout.setContentsMargins(0, 40, 0, 0)

        # Logo + company name
        logo_circle = QFrame()
        logo_circle.setObjectName("LogoCircle")
        logo_circle.setFixedSize(96, 96)
        logo_layout = QVBoxLayout(logo_circle)
        logo_layout.setAlignment(Qt.AlignCenter)
        logo_icon = QLabel("🏭")
        logo_icon.setAlignment(Qt.AlignCenter)
        logo_icon.setStyleSheet("font-size: 32px; color: white;")
        logo_layout.addWidget(logo_icon)

        company_label = QLabel("GAMALITE SDN BHD")
        company_label.setObjectName("CompanyLabel")

        dept_label = QLabel("Quality Control Department")
        dept_label.setObjectName("DeptLabel")

        container_layout.addWidget(logo_circle, 0, Qt.AlignHCenter)
        container_layout.addSpacing(12)
        container_layout.addWidget(company_label, 0, Qt.AlignHCenter)
        container_layout.addWidget(dept_label, 0, Qt.AlignHCenter)
        container_layout.addSpacing(24)

        # Card
        card = QFrame()
        card.setObjectName("LoginCard")
        card.setFixedWidth(420)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(28, 28, 28, 24)
        card_layout.setSpacing(16)

        # Card header text
        welcome_title = QLabel("Welcome to GAMALITE SDN BHD")
        welcome_title.setObjectName("WelcomeTitle")

        sub_title = QLabel("QC Login Portal")
        sub_title.setObjectName("SubTitle")

        card_layout.addWidget(welcome_title, 0, Qt.AlignHCenter)
        card_layout.addWidget(sub_title, 0, Qt.AlignHCenter)
        card_layout.addSpacing(10)

        # Username field
        username_label = QLabel("👤  Username:")
        username_label.setObjectName("FieldLabel")
        self.username_edit = QLineEdit()
        self.username_edit.setPlaceholderText("Enter your username")
        self.username_edit.textChanged.connect(self._force_uppercase)
        self.username_edit.setObjectName("TextField")

        # Password field
        password_label = QLabel("🔒  Password:")
        password_label.setObjectName("FieldLabel")
        self.password_edit = QLineEdit()
        self.password_edit.setPlaceholderText("Enter your password")
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.setObjectName("TextField")
        # ✅ Press Enter to login (works in username OR password box)
        self.username_edit.returnPressed.connect(self.handle_login)
        self.password_edit.returnPressed.connect(self.handle_login)


        card_layout.addWidget(username_label)
        card_layout.addWidget(self.username_edit)
        card_layout.addWidget(password_label)
        card_layout.addWidget(self.password_edit)

        # Login button
        self.login_btn = QPushButton("  Login to QC Portal")
        self.login_btn.setObjectName("LoginButton")
        self.login_btn.setMinimumHeight(40)
        self.login_btn.clicked.connect(self.handle_login)
        card_layout.addWidget(self.login_btn)
        # --- Sign up link row (like sample) ---
        self.signup_label = QLabel("Don't have an account? <a href='signup'>Sign up</a>")
        self.signup_label.setObjectName("SignupLabel")
        self.signup_label.setTextFormat(Qt.RichText)
        self.signup_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.signup_label.setOpenExternalLinks(False)  # handle click ourselves
        self.signup_label.setAlignment(Qt.AlignHCenter)
        self.signup_label.linkActivated.connect(self._open_signup_flow)

        # --- Sign up container (forces white background) ---
        signup_container = QWidget()
        signup_container.setStyleSheet("background-color: #FFFFFF;")

        signup_layout = QHBoxLayout(signup_container)
        signup_layout.setContentsMargins(0, 6, 0, 0)
        signup_layout.setAlignment(Qt.AlignCenter)

        signup_layout.addWidget(self.signup_label)

        card_layout.addWidget(signup_container)



        # Add card into container
        container_layout.addWidget(card, 0, Qt.AlignHCenter)

        root_layout.addWidget(container, 1)

        # Footer
        footer = QVBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(2)

        copyright_label = QLabel(
            "© 2024 GAMALITE SDN BHD. All rights reserved."
        )
        copyright_label.setObjectName("FooterLabel")
        version_label = QLabel(
            "Version 2.1.0 | Quality Control System"
        )
        version_label.setObjectName("FooterLabel")

        footer.addWidget(copyright_label, 0, Qt.AlignHCenter)
        footer.addWidget(version_label, 0, Qt.AlignHCenter)

        root_layout.addLayout(footer)

    def _open_signup_flow(self, _link="signup"):
        # 1) Ask admin login
        dlg = AdminLoginDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return

        # 2) Admin OK → open Sign Up dialog
        reg = QCRegistrationDialog(self)
        reg.exec()



    def _apply_styles(self):
            self.setStyleSheet("""
            QMainWindow, QWidget {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                        stop:0 #EFF6FF, stop:1 #ECFDF5);
                font-family: "Segoe UI", Arial, sans-serif;
                color: #111827;
                font-size: 10pt;
            }
            #LogoCircle {
                border-radius: 48px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                            stop:0 #2563EB, stop:1 #16A34A);
            }
            #CompanyLabel {
                font-size: 16pt;
                font-weight: 700;
                letter-spacing: 1px;
                color: #111827;
            }
            #DeptLabel {
                color: #6B7280;
                font-size: 10pt;
            }
            #LoginCard {
                background-color: #FFFFFF;
                border-radius: 16px;
                border: 1px solid #E5E7EB;
            }
            #WelcomeTitle {
                font-size: 12.5pt;
                font-weight: 600;
                color: #111827;
                background-color: #FFFFFF;
            }
            #SubTitle {
                font-size: 10pt;
                color: #6B7280;
                background-color: #FFFFFF;
            }
            #FieldLabel {
                font-size: 9.5pt;
                color: #374151;
                background-color: #FFFFFF;
            }
            #TextField {
                border-radius: 8px;
                border: 1px solid #E5E7EB;
                padding: 8px 10px;
                background: #FFFFFF;
            }
            #TextField:focus {
                border: 1px solid #2563EB;
            }
            #RememberCheck {
                color: #4B5563;
            }
            #ForgotLabel {
                color: #2563EB;
                font-size: 9.5pt;
                background-color: #FFFFFF;
                padding: 2px 6px;
                border-radius: 4px;
            }
            #LoginButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                            stop:0 #2563EB, stop:1 #16A34A);
                color: #FFFFFF;
                font-weight: 600;
                border-radius: 8px;
                border: none;
            }
            #LoginButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                            stop:0 #2b6ce8, stop:1 #1aa147);
            }

            #HelpLabel {
                color: #6B7280;
                font-size: 9pt;
            }
            #FooterLabel {
                color: #9CA3AF;
                font-size: 8.5pt;
            }
            """)
    def _force_uppercase(self, text):
        cursor_pos = self.username_edit.cursorPosition()
        self.username_edit.blockSignals(True)
        self.username_edit.setText(text.upper())
        self.username_edit.setCursorPosition(cursor_pos)
        self.username_edit.blockSignals(False)

    def handle_login(self):
        username = self.username_edit.text().strip()
        password = self.password_edit.text().strip()

        # ✅ reset styles first (remove red border if previously failed)
        self.username_edit.setStyleSheet("")
        self.password_edit.setStyleSheet("")

        # ----- MySQL (READ-ONLY) login check -----
        login_ok = False
        if DB_ENABLED:
            try:
                conn = mysql.connector.connect(
                    host=DB_HOST,
                    port=DB_PORT,
                    user=DB_USER,          # ✅ for true read-only: create a MySQL user with SELECT only and put it here
                    password=DB_PASSWORD,
                    database=DB_NAME,
                    use_pure=True,
                )
                cur = conn.cursor()

                cur.execute(
                    f"""
                    SELECT pass_salt, pass_hash
                    FROM {USERS_TABLE}
                    WHERE username = %s
                    LIMIT 1
                    """,
                    (username,)
                )
                row = cur.fetchone()

                cur.close()
                conn.close()

                if row and row[0] and row[1]:
                    salt = row[0]
                    stored_hash = row[1]
                    login_ok = verify_password(password, salt, stored_hash)

            except Exception as e:
                print("[LOGIN] DB error:", e)
                login_ok = False

        if login_ok:
            print("[DEBUG] Login OK, opening Home Dashboard.")

            # hide login first (smooth)
            self.hide()

            # create dashboard (do not show inside dashboard __init__)
            self.main_window = MainDashboardWindow(current_user=username)


            # refresh stats if function exists
            if hasattr(self.main_window, "refresh_overall_home_stats"):
                self.main_window.refresh_overall_home_stats()

            # show dashboard
            self.main_window.showMaximized()
            self.main_window.raise_()
            self.main_window.activateWindow()

            # close login
            self.close()

        else:
            # red border
            self.username_edit.setStyleSheet(
                "border: 1px solid #DC2626; border-radius: 8px; padding: 8px 10px;"
            )
            self.password_edit.setStyleSheet(
                "border: 1px solid #DC2626; border-radius: 8px; padding: 8px 10px;"
            )

            QMessageBox.warning(self, "Login Failed", "Invalid username or password.")

            # UX: clear password + focus it
            self.password_edit.clear()
            self.password_edit.setFocus()

class QCRegistrationDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("QC Registration")
        self.setModal(True)
        self.setFixedSize(420, 650)

        self.setStyleSheet("""
            QDialog#QCRegistrationDialog, QWidget#QCRegistrationDialog {
                background-color: #FFFFFF;
            }
            #Card {
                background-color: #FFFFFF;
                border-radius: 16px;
                border: 1px solid #E5E7EB;
            }
            QLabel#Title { font-size: 18pt; font-weight: 800; color: #111827; background: transparent; }
            QLabel#SubTitle { font-size: 10pt; color: #6B7280; background: transparent; }
            QLabel#Field { font-size: 10pt; font-weight: 600; color: #111827; background: transparent; }

            QLineEdit {
                background-color: #FFFFFF;
                border: 1px solid #D1D5DB;
                border-radius: 10px;
                padding: 10px 12px;
                font-size: 11pt;
            }
            QLineEdit:focus { border: 2px solid #2563EB; }

            QCheckBox { background: transparent; }

            QPushButton#SignUpBtn {
                background-color: #2563EB;
                color: #FFFFFF;
                font-weight: 700;
                border-radius: 10px;
                border: none;
                padding: 12px;
                min-height: 48px;
            }
            QPushButton#SignUpBtn:hover { background-color: #1D4ED8; }

            QPushButton#CloseBtn {
                background-color: #E5E7EB;
                color: #111827;
                font-weight: 700;
                border-radius: 10px;
                border: none;
                padding: 12px;
                min-height: 48px;
            }
            QPushButton#CloseBtn:hover { background-color: #D1D5DB; }
            QPushButton#SignupPrimaryButton {
                background-color: #2563EB;
                color: #FFFFFF;
                font-weight: 700;
                border-radius: 10px;
                border: none;
            }
            QPushButton#SignupPrimaryButton:hover { background-color: #1D4ED8; }

            QPushButton#SignupCloseButton {
                background-color: #DC2626;
                color: #FFFFFF;
                font-weight: 700;
                border-radius: 10px;
                border: none;
            }
            QPushButton#SignupCloseButton:hover { background-color: #B91C1C; }

            """)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)

        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(14)

        # ---- Icon container (force white background) ----
        icon_wrap = QFrame()
        icon_wrap.setFixedHeight(80)
        icon_wrap.setStyleSheet("""
            background-color: #FFFFFF;
            border-radius: 12px;
        """)

        icon_layout = QVBoxLayout(icon_wrap)
        icon_layout.setContentsMargins(0, 10, 0, 10)

        icon = QLabel("📋")
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet("font-size: 36px; background: transparent;")

        icon_layout.addWidget(icon)
        lay.addWidget(icon_wrap)


        title = QLabel("QC Registration")
        title.setObjectName("Title")
        title.setAlignment(Qt.AlignCenter)
        lay.addWidget(title)

        subtitle = QLabel("Create your Quality Control account to get started")
        subtitle.setObjectName("SubTitle")
        subtitle.setAlignment(Qt.AlignCenter)
        lay.addWidget(subtitle)

        lay.addSpacing(8)

        u_lbl = QLabel("Username")
        u_lbl.setObjectName("Field")
        lay.addWidget(u_lbl)
        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("Enter your username")
        lay.addWidget(self.user_edit)

        p_lbl = QLabel("Password")
        p_lbl.setObjectName("Field")
        lay.addWidget(p_lbl)
        self.pass_edit = QLineEdit()
        self.pass_edit.setEchoMode(QLineEdit.Password)
        self.pass_edit.setPlaceholderText("Create a strong password")
        lay.addWidget(self.pass_edit)

        c_lbl = QLabel("Confirm Password")
        c_lbl.setObjectName("Field")
        lay.addWidget(c_lbl)
        self.confirm_edit = QLineEdit()
        self.confirm_edit.setEchoMode(QLineEdit.Password)
        self.confirm_edit.setPlaceholderText("Re-enter your password")
        lay.addWidget(self.confirm_edit)

        self.terms = QCheckBox("I agree to the Terms and Conditions and Privacy Policy")
        self.terms.setChecked(True)
        lay.addWidget(self.terms)

        btns = QHBoxLayout()
        btns.setSpacing(12)

        self.btn_signup = QPushButton("Sign Up") 
        self.btn_signup.setObjectName("SignupPrimaryButton")           
        self.btn_signup.setMinimumHeight(48)
        self.btn_signup.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.btn_signup.clicked.connect(self._do_signup)

        self.btn_close = QPushButton("Close")
        self.btn_close.setObjectName("SignupCloseButton")       
        self.btn_close.setMinimumHeight(48)
        self.btn_close.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.btn_close.clicked.connect(self.reject)

        btns.addWidget(self.btn_signup)
        btns.addWidget(self.btn_close)
        lay.addLayout(btns)

        self.btn_signup.setDefault(True)
        self.btn_signup.setAutoDefault(True)

        self.confirm_edit.returnPressed.connect(self._do_signup)
        self.pass_edit.returnPressed.connect(self._do_signup)

        root.addWidget(card)

    def _do_signup(self):
        u = self.user_edit.text().strip()
        p1 = self.pass_edit.text().strip()
        p2 = self.confirm_edit.text().strip()

        # --- UI validation ---
        if not u or not p1 or not p2:
            QMessageBox.warning(self, "Error", "Please fill all fields.")
            return
        if p1 != p2:
            QMessageBox.warning(self, "Error", "Passwords do not match.")
            return
        if not self.terms.isChecked():
            QMessageBox.warning(self, "Error", "Please agree to the terms.")
            return

        # --- DB check ---
        if not DB_ENABLED:
            QMessageBox.warning(self, "DB Disabled", "DB is disabled in qc_form.py (DB_ENABLED=False).")
            return

        # --- Hash password (will look random in MySQL) ---
        salt, pwd_hash = hash_password(p1)

        try:
            conn = mysql.connector.connect(
                host=DB_HOST,
                port=DB_PORT,
                user=DB_USER,
                password=DB_PASSWORD,
                database=DB_NAME,
                use_pure=True,
            )
            cur = conn.cursor()

            # insert new user
            cur.execute(
                f"INSERT INTO {USERS_TABLE} (username, pass_salt, pass_hash) VALUES (%s, %s, %s)",
                (u, salt, pwd_hash),
            )

            conn.commit()
            cur.close()
            conn.close()

            QMessageBox.information(self, "Success", f"User '{u}' registered successfully.")
            self.accept()

        except mysql.connector.IntegrityError:
            # happens when username UNIQUE and already exists
            QMessageBox.warning(self, "Duplicate", "Username already exists. Please choose another.")
        except Exception as e:
            QMessageBox.critical(self, "Database Error", f"Failed to register:\n{e}")




# ------------------- Main ------------------- #

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = LoginWindow()
    win.show()
    sys.exit(app.exec())