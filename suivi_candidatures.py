# -*- coding: utf-8 -*-
"""
Application Desktop de suivi des candidatures
------------------------------------------------
- Python 3.9+
- PySide6
- SQLite (fichier local jobtrack.db)

Fonctionnalités (conformes au cahier des charges) :
✅ CRUD complet (Ajouter / Modifier / Supprimer)
✅ Tableau triable + recherche texte + filtres (statut, priorité) + tri par colonnes
✅ Rappels de date limite (paramétrable, par défaut 3 jours) + badge comptage
✅ Export CSV / Import CSV
✅ Sauvegarde auto à chaque opération (SQLite)
✅ Modèle SQL Qt pour meilleures perfs (QSqlTableModel + QSortFilterProxyModel)

Exécution :
    pip install PySide6
    python app_suivi_candidatures.py

Le fichier de base de données (jobtrack.db) sera créé automatiquement au premier lancement.
"""

from __future__ import annotations
import csv
import os
import sys
import typing as t
from datetime import datetime, timedelta

from PySide6.QtCore import (QAbstractTableModel, QDate, QItemSelectionModel,
                            QLocale, QRegularExpression, QSortFilterProxyModel,
                            Qt, QTimer, Signal, Slot)
from PySide6.QtGui import QAction, QIcon
from PySide6.QtSql import QSqlDatabase, QSqlQuery, QSqlTableModel
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QMenu, QMenuBar, QMessageBox, QPushButton,
    QSizePolicy, QSpinBox, QStatusBar, QTableView, QTextEdit, QToolBar,
    QVBoxLayout, QWidget
)

from PySide6.QtCore import QStandardPaths
import os

APP_NAME = "Suivi Candidatures"

# Dossier AppData local
data_dir = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
os.makedirs(data_dir, exist_ok=True)
DB_PATH = os.path.join(data_dir, "jobtrack.db")
TABLE_NAME = "candidatures"

PRIORITES = ["Faible", "Moyenne", "Élevée"]
STATUTS = [
    "À postuler",
    "Envoyé",
    "En attente",
    "Relancé",
    "Refusé",
    "Accepté",
]

CANAL_ENVOI = [
    "Email",
    "Plateforme",
    "Site carrière",
    "Recommandation",
    "Autre",
]

DATE_FMT = "%Y-%m-%d"  # ISO pour SQLite et cohérence


def ensure_database(path: str = DB_PATH) -> None:
    """Crée le fichier SQLite et la table si nécessaire."""
    need_init = not os.path.exists(path)

    db = QSqlDatabase.addDatabase("QSQLITE")
    db.setDatabaseName(path)
    if not db.open():
        QMessageBox.critical(None, APP_NAME, f"Impossible d'ouvrir la base de données : {db.lastError().text()}")
        sys.exit(1)

    if need_init:
        q = QSqlQuery()
        ok = q.exec(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                numero TEXT,
                titre TEXT NOT NULL,
                structure TEXT,
                date_limite TEXT,     -- YYYY-MM-DD
                priorite TEXT,
                canal_envoi TEXT,
                statut TEXT,
                date_envoi TEXT,      -- YYYY-MM-DD
                notes TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        if not ok:
            QMessageBox.critical(None, APP_NAME, f"Erreur création table : {q.lastError().text()}")
            sys.exit(1)


class CandidatureDialog(QDialog):
    """Fenêtre d'ajout / édition d'une candidature."""

    def __init__(self, parent: QWidget | None = None, data: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Candidature")
        self.setMinimumWidth(480)

        self.numero = QLineEdit()
        self.titre = QLineEdit()
        self.structure = QLineEdit()

        self.date_limite = QDateEdit(calendarPopup=True)
        self.date_limite.setDisplayFormat("yyyy-MM-dd")
        self.date_limite.setDate(QDate.currentDate())
        self.date_limite.setSpecialValueText("")
        self.date_limite.setMinimumDate(QDate(1970, 1, 1))

        self.priorite = QComboBox(); self.priorite.addItems(PRIORITES)
        self.canal = QComboBox(); self.canal.addItems(CANAL_ENVOI)
        self.statut = QComboBox(); self.statut.addItems(STATUTS)

        self.date_envoi = QDateEdit(calendarPopup=True)
        self.date_envoi.setDisplayFormat("yyyy-MM-dd")
        self.date_envoi.setSpecialValueText("")
        self.date_envoi.setDate(QDate.currentDate())
        self.date_envoi.setMinimumDate(QDate(1970, 1, 1))

        self.notes = QTextEdit()
        self.notes.setPlaceholderText("Notes / mots-clés, etc.")

        form = QFormLayout()
        form.addRow("N°", self.numero)
        form.addRow("Titre*", self.titre)
        form.addRow("Structure", self.structure)
        form.addRow("Date limite", self.date_limite)
        form.addRow("Priorité", self.priorite)
        form.addRow("Canal d'envoi", self.canal)
        form.addRow("Statut", self.statut)
        form.addRow("Date d'envoi", self.date_envoi)
        form.addRow("Notes", self.notes)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.buttons)

        if data:
            self.load_data(data)

    def load_data(self, d: dict) -> None:
        self.numero.setText(d.get("numero", "") or "")
        self.titre.setText(d.get("titre", "") or "")
        self.structure.setText(d.get("structure", "") or "")

        def set_qdate(widget: QDateEdit, value: str | None):
            if value:
                try:
                    dt = datetime.strptime(value, DATE_FMT)
                    widget.setDate(QDate(dt.year, dt.month, dt.day))
                except Exception:
                    pass

        set_qdate(self.date_limite, d.get("date_limite"))
        set_qdate(self.date_envoi, d.get("date_envoi"))

        if d.get("priorite") in PRIORITES:
            self.priorite.setCurrentText(d["priorite"])
        if d.get("canal_envoi") in CANAL_ENVOI:
            self.canal.setCurrentText(d["canal_envoi"])
        if d.get("statut") in STATUTS:
            self.statut.setCurrentText(d["statut"])

        self.notes.setPlainText(d.get("notes", "") or "")

    def get_data(self) -> dict | None:
        titre = self.titre.text().strip()
        if not titre:
            QMessageBox.warning(self, APP_NAME, "Le champ Titre est obligatoire.")
            return None

        def qdate_to_str(widget: QDateEdit) -> str | None:
            if not widget.date().isValid():
                return None
            return widget.date().toString("yyyy-MM-dd")

        return {
            "numero": self.numero.text().strip() or None,
            "titre": titre,
            "structure": self.structure.text().strip() or None,
            "date_limite": qdate_to_str(self.date_limite),
            "priorite": self.priorite.currentText(),
            "canal_envoi": self.canal.currentText(),
            "statut": self.statut.currentText(),
            "date_envoi": qdate_to_str(self.date_envoi),
            "notes": self.notes.toPlainText().strip() or None,
        }


class CandidatureFilterProxy(QSortFilterProxyModel):
    """Proxy pour recherche texte + filtres statut/priorité + date limite max."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.search_regex = QRegularExpression("")
        self.filter_statut: str | None = None
        self.filter_priorite: str | None = None
        self.max_deadline: str | None = None  # YYYY-MM-DD

    def set_search_text(self, text: str):
        self.search_regex = QRegularExpression(text, QRegularExpression.CaseInsensitiveOption)
        self.invalidateFilter()

    def set_filter_statut(self, statut: str | None):
        self.filter_statut = statut if statut and statut != "(Tous)" else None
        self.invalidateFilter()

    def set_filter_priorite(self, priorite: str | None):
        self.filter_priorite = priorite if priorite and priorite != "(Toutes)" else None
        self.invalidateFilter()

    def set_max_deadline(self, date_str: str | None):
        self.max_deadline = date_str
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:
        model = self.sourceModel()
        if model is None:
            return True

        def data(col_name: str) -> str:
            idx = model.index(source_row, model.fieldIndex(col_name), source_parent)
            return (model.data(idx) or "").lower()

        # Recherche texte (dans plusieurs colonnes)
        if self.search_regex.pattern():
            hay = " ".join([
                data("numero"), data("titre"), data("structure"),
                data("priorite"), data("canal_envoi"), data("statut"), data("notes")
            ])
            if not self.search_regex.match(hay).hasMatch():
                return False

        # Filtre statut
        if self.filter_statut is not None:
            if data("statut") != self.filter_statut.lower():
                return False

        # Filtre priorité
        if self.filter_priorite is not None:
            if data("priorite") != self.filter_priorite.lower():
                return False

        # Filtre date limite max
        if self.max_deadline:
            dl_idx = model.index(source_row, model.fieldIndex("date_limite"), source_parent)
            dl_val = model.data(dl_idx)
            if dl_val:
                try:
                    if dl_val > self.max_deadline:
                        return False
                except Exception:
                    pass

        return True


class MainWindow(QMainWindow):
    remindThresholdDaysChanged = Signal(int)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1100, 680)

        # Paramètres de rappel
        self.remind_days = 3

        # Modèle SQL
        self.model = QSqlTableModel(self)
        self.model.setTable(TABLE_NAME)
        self.model.setEditStrategy(QSqlTableModel.OnFieldChange)
        self.model.select()

        # Proxy pour recherches/tri
        self.proxy = CandidatureFilterProxy(self)
        self.proxy.setSourceModel(self.model)

        # Table
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)

        # Masquer colonnes techniques
        self.hide_columns(["id", "created_at"])

        # Barre d'outils et filtres
        toolbar = QToolBar("Actions")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

         # --- Thème clair / sombre
        self.dark_mode = False

        btn_theme = QAction("Mode sombre", self)
        btn_theme.triggered.connect(self.toggle_theme)
        toolbar.addSeparator()
        toolbar.addAction(btn_theme)

        self.btn_theme = btn_theme  # pour pouvoir changer le texte plus tard


        btn_add = QAction("Ajouter", self); btn_add.triggered.connect(self.add_record)
        btn_edit = QAction("Modifier", self); btn_edit.triggered.connect(self.edit_selected)
        btn_del = QAction("Supprimer", self); btn_del.triggered.connect(self.delete_selected)
        btn_import = QAction("Importer CSV", self); btn_import.triggered.connect(self.import_csv)
        btn_export = QAction("Exporter CSV", self); btn_export.triggered.connect(self.export_csv)
        btn_refresh = QAction("Rafraîchir", self); btn_refresh.triggered.connect(self.model.select)

        toolbar.addAction(btn_add)
        toolbar.addAction(btn_edit)
        toolbar.addAction(btn_del)
        toolbar.addSeparator()
        toolbar.addAction(btn_import)
        toolbar.addAction(btn_export)
        toolbar.addSeparator()
        toolbar.addAction(btn_refresh)

        # Filtres/recherche
        filters_box = QGroupBox("Recherche & Filtres")
        grid = QGridLayout(filters_box)

        self.search_edit = QLineEdit(); self.search_edit.setPlaceholderText("Rechercher… (titre, structure, notes…)")
        self.search_edit.textChanged.connect(self.proxy.set_search_text)

        self.cb_statut = QComboBox(); self.cb_statut.addItem("(Tous)"); self.cb_statut.addItems(STATUTS)
        self.cb_statut.currentTextChanged.connect(lambda s: self.proxy.set_filter_statut(s))

        self.cb_priorite = QComboBox(); self.cb_priorite.addItem("(Toutes)"); self.cb_priorite.addItems(PRIORITES)
        self.cb_priorite.currentTextChanged.connect(lambda s: self.proxy.set_filter_priorite(s))

        self.deadline_max = QDateEdit(calendarPopup=True); self.deadline_max.setDisplayFormat("yyyy-MM-dd")
        self.deadline_max.setSpecialValueText("(Illimité)")
        self.deadline_max.setDate(QDate.currentDate().addYears(50))
        self.deadline_max.dateChanged.connect(lambda d: self.proxy.set_max_deadline(d.toString("yyyy-MM-dd")))

        self.spin_remind = QSpinBox(); self.spin_remind.setRange(0, 60); self.spin_remind.setValue(self.remind_days)
        self.spin_remind.setSuffix(" j")
        self.spin_remind.valueChanged.connect(self.on_remind_days_changed)

        self.lbl_alerts = QLabel("")
        self.lbl_alerts.setStyleSheet("color:#b00020; font-weight:600;")

        grid.addWidget(QLabel("Recherche"), 0, 0)
        grid.addWidget(self.search_edit, 0, 1, 1, 3)
        grid.addWidget(QLabel("Statut"), 1, 0)
        grid.addWidget(self.cb_statut, 1, 1)
        grid.addWidget(QLabel("Priorité"), 1, 2)
        grid.addWidget(self.cb_priorite, 1, 3)
        grid.addWidget(QLabel("Date limite ≤"), 2, 0)
        grid.addWidget(self.deadline_max, 2, 1)
        grid.addWidget(QLabel("Rappel (jours)"), 2, 2)
        grid.addWidget(self.spin_remind, 2, 3)
        grid.addWidget(self.lbl_alerts, 3, 0, 1, 4)

        # Layout central
        central = QWidget()
        v = QVBoxLayout(central)
        v.addWidget(filters_box)
        v.addWidget(self.table)
        self.setCentralWidget(central)

        # Barre de statut
        self.status = QStatusBar(); self.setStatusBar(self.status)

        # Timer de rappel
        self.timer = QTimer(self)
        self.timer.setInterval(60 * 1000)  # vérif toutes les 60s
        self.timer.timeout.connect(self.update_reminders)
        self.timer.start()

        # Vérification initiale
        QTimer.singleShot(500, self.update_reminders)

    # --- Utilitaires colonnes
    def hide_columns(self, names: list[str]):
        for name in names:
            col = self.model.fieldIndex(name)
            if col >= 0:
                self.table.setColumnHidden(self.proxy.mapFromSource(self.model.index(0, col)).column(), True)
                
    def _update_row(self, row: int, d: dict):
        """Met à jour une ligne existante dans la base SQLite."""
        # Récupération de l'ID de la ligne
        id_col = self.model.fieldIndex("id")
        id_val = self.model.data(self.model.index(row, id_col))
        if not id_val:
            QMessageBox.critical(self, APP_NAME, "Impossible de retrouver l'ID de la candidature.")
            return

        # Préparation de la requête SQL
        q = QSqlQuery()
        q.prepare(f"""
            UPDATE {TABLE_NAME} SET
                numero=:numero,
                titre=:titre,
                structure=:structure,
                date_limite=:date_limite,
                priorite=:priorite,
                canal_envoi=:canal_envoi,
                statut=:statut,
                date_envoi=:date_envoi,
                notes=:notes
            WHERE id=:id
        """)

        # Liaison des valeurs
        for k, v in d.items():
            q.bindValue(f":{k}", v)
        q.bindValue(":id", id_val)

        # Exécution
        if not q.exec():
            QMessageBox.critical(self, APP_NAME, f"Erreur lors de la mise à jour : {q.lastError().text()}")

    # --- CRUD
    @Slot()
    def add_record(self):
        dlg = CandidatureDialog(self)
        if dlg.exec() == QDialog.Accepted:
            data = dlg.get_data()
            if data is None:
                return
            self._insert_row(data)
            self.model.select()
            self.status.showMessage("Candidature ajoutée", 2500)

    def _insert_row(self, d: dict):
        q = QSqlQuery()
        q.prepare(
            f"""
            INSERT INTO {TABLE_NAME}
            (numero, titre, structure, date_limite, priorite, canal_envoi, statut, date_envoi, notes)
            VALUES (:numero, :titre, :structure, :date_limite, :priorite, :canal_envoi, :statut, :date_envoi, :notes)
            """
        )
        for k, v in d.items():
            q.bindValue(f":{k}", v)
        if not q.exec():
            QMessageBox.critical(self, APP_NAME, f"Erreur d'insertion : {q.lastError().text()}")

    @Slot()
    def edit_selected(self):
        idx = self._current_source_index()
        if not idx.isValid():
            QMessageBox.information(self, APP_NAME, "Sélectionnez une ligne à modifier.")
            return
        row = idx.row()
        data = self._row_to_dict(row)
        dlg = CandidatureDialog(self, data)
        if dlg.exec() == QDialog.Accepted:
            new_data = dlg.get_data()
            if new_data is None:
                return
            self._update_row(row, new_data)
            self.model.select()
            self.status.showMessage("Candidature modifiée", 2500)

    @Slot()
    def delete_selected(self):
        idx = self._current_source_index()
        if not idx.isValid():
            QMessageBox.information(self, APP_NAME, "Sélectionnez une ligne à supprimer.")
            return
        row = idx.row()
        id_col = self.model.fieldIndex("id")
        id_val = self.model.data(self.model.index(row, id_col))
        if QMessageBox.question(self, APP_NAME, "Confirmer la suppression ?") == QMessageBox.Yes:
            q = QSqlQuery()
            if not q.exec(f"DELETE FROM {TABLE_NAME} WHERE id = {int(id_val)}"):
                QMessageBox.critical(self, APP_NAME, f"Erreur de suppression : {q.lastError().text()}")
            else:
                self.model.select()
                self.status.showMessage("Candidature supprimée", 2500)

    # --- Import / Export
    @Slot()
    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Exporter CSV", "candidatures.csv", "CSV (*.csv)")
        if not path:
            return
        headers = [
            "id","numero","titre","structure","date_limite","priorite",
            "canal_envoi","statut","date_envoi","notes","created_at"
        ]
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for r in range(self.model.rowCount()):
                row = []
                for h in headers:
                    col = self.model.fieldIndex(h)
                    row.append(self.model.data(self.model.index(r, col)))
                writer.writerow(row)
        self.status.showMessage(f"Exporté vers {path}", 4000)
    
    def toggle_theme(self):
        """Bascule entre le mode clair et sombre."""
        if not self.dark_mode:
            # Mode sombre
            self.setStyleSheet("""
                QMainWindow { background-color: #121212; color: #eee; }
                QWidget { background-color: #121212; color: #eee; }
                QLineEdit, QTextEdit, QComboBox, QDateEdit, QSpinBox {
                    background-color: #1e1e1e; color: #eee; border: 1px solid #444;
                }
                QTableView {
                    background-color: #1e1e1e;
                    alternate-background-color: #2a2a2a;
                    color: #eee;
                    gridline-color: #444;
                }
                QHeaderView::section {
                    background-color: #2a2a2a;
                    color: #eee;
                    padding: 4px;
                    border: 1px solid #444;
                }
            """)
            self.btn_theme.setText("Mode clair")
            self.dark_mode = True
        else:
            # Mode clair
            self.setStyleSheet("")
            self.btn_theme.setText("Mode sombre")
            self.dark_mode = False


    @Slot()
    def import_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "Importer CSV", "", "CSV (*.csv)")
        if not path:
            return
        # Import tolérant : si 'id' existe et correspond, on met à jour; sinon on insère.
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                payload = {
                    "numero": row.get("numero") or None,
                    "titre": row.get("titre") or None,
                    "structure": row.get("structure") or None,
                    "date_limite": row.get("date_limite") or None,
                    "priorite": row.get("priorite") or None,
                    "canal_envoi": row.get("canal_envoi") or None,
                    "statut": row.get("statut") or None,
                    "date_envoi": row.get("date_envoi") or None,
                    "notes": row.get("notes") or None,
                }
                rid = row.get("id")
                if rid:
                    # Update si l'enregistrement existe
                    q = QSqlQuery()
                    q.prepare(
                        f"""UPDATE {TABLE_NAME} SET
                            numero=:numero, titre=:titre, structure=:structure,
                            date_limite=:date_limite, priorite=:priorite,
                            canal_envoi=:canal_envoi, statut=:statut,
                            date_envoi=:date_envoi, notes=:notes
                        WHERE id=:id"""
                    )
                    for k, v in payload.items():
                        q.bindValue(f":{k}", v)
                    q.bindValue(":id", rid)
                    if not q.exec():
                        # si update échoue, on tente un insert
                        self._insert_row(payload)
                else:
                    self._insert_row(payload)
        self.model.select()
        self.status.showMessage(f"Import depuis {path} terminé", 4000)

    # --- Rappels
    def on_remind_days_changed(self, days: int):
        self.remind_days = int(days)
        self.update_reminders()

    def update_reminders(self):
        # Compte des candidatures dont la date limite est aujourd'hui ou dans N jours
        n = self.count_deadline_within(self.remind_days)
        if n > 0:
            self.lbl_alerts.setText(f"⚠ {n} candidature(s) avec date limite ≤ {self.remind_days} j")
        else:
            self.lbl_alerts.setText("")

    def count_deadline_within(self, days: int) -> int:
        q = QSqlQuery()
        today = datetime.today().strftime(DATE_FMT)
        limit = (datetime.today() + timedelta(days=days)).strftime(DATE_FMT)
        q.prepare(
            f"SELECT COUNT(*) FROM {TABLE_NAME} WHERE statut = 'À postuler' AND date_limite IS NOT NULL AND date_limite BETWEEN :d1 AND :d2"
        )
        q.bindValue(":d1", today)
        q.bindValue(":d2", limit)
        if not q.exec():
            return 0
        return q.next() and q.value(0) or 0

    # --- Helpers
    def _current_source_index(self):
        idx = self.table.currentIndex()
        return self.proxy.mapToSource(idx)

    def _row_to_dict(self, row: int) -> dict:
        fields = [
            "id","numero","titre","structure","date_limite","priorite",
            "canal_envoi","statut","date_envoi","notes","created_at"
        ]
        out = {}
        for f in fields:
            col = self.model.fieldIndex(f)
            out[f] = self.model.data(self.model.index(row, col))
        return out


def main():
    # Locale FR pour les widgets de date (affichage calendrier, etc.)
    QLocale.setDefault(QLocale(QLocale.French, QLocale.France))
    app = QApplication(sys.argv)

    ensure_database(DB_PATH)

    w = MainWindow()
    w.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
