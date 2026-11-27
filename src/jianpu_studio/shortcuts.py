"""F1: searchable, non-modal shortcut reference (wow/design.md §5)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLineEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout

from .i18n import N_, _portable_keys, tr
from .widgets import label

GROUPS = (
    (N_("輸入"), ((N_("1–7 / 0"), N_("輸入音符 / 休止符")), (N_("Q W E R A S"), N_("全、二分、四分、八分、十六分、三十二分")),
                (N_("."), N_("附點")), (N_("↑ ↓"), N_("高／低八度")), (N_("[ ]"), N_("縮短／延長時值")),
                (N_("空白鍵"), N_("切換新增／修改")))),
    (N_("選取"), ((N_("Shift＋點"), N_("連續選取")), (N_("Ctrl＋點"), N_("加選")), (N_("Ctrl+A"), N_("全選")),
                (N_("M"), N_("選取整小節")), (N_("Esc"), N_("取消選取")), (N_("Alt+↑ / Alt+↓"), N_("切換聲部")),
                (N_("F6"), N_("在譜面、記號面板、選取區、曲目列之間移動")))),
    (N_("小節"), ((N_("Insert"), N_("前插一小節")), (N_("Ctrl+Insert"), N_("新增多個小節")), (N_("Ctrl+B"), N_("曲尾加一小節")),
                (N_("Ctrl+D"), N_("重複")), (N_("Ctrl+Delete"), N_("刪除小節")))),
    (N_("記號"), ((N_("/ 或 Ctrl+F"), N_("搜尋記號")), (N_("Enter"), N_("套用並回到譜面（在搜尋結果中）")),
                (N_("Ctrl+Enter"), N_("套用並留在面板")), (N_("Shift+Enter"), N_("連續套用")),
                (N_("Alt+1…9"), N_("套用常用第 1–9 個")), (N_("F4"), N_("收合／展開記號面板")))),
    (N_("試聽與檔案"), ((N_("Enter"), N_("試聽／停止（譜面）")), (N_("Ctrl+S"), N_("儲存")), (N_("Ctrl+O"), N_("開啟")),
                     (N_("Ctrl+N"), N_("新建")), (N_("Ctrl+Z / Ctrl+Shift+Z"), N_("復原／重做")),
                     (N_("Ctrl+C / X / V"), N_("複製／剪下／貼上")), (N_("F5"), N_("列印預覽")),
                     (N_("Ctrl+E"), N_("匯出 PDF")), (N_("Ctrl+= / Ctrl+- / Ctrl+0"), N_("放大／縮小／100%")))),
)


class ShortcutsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("shortcuts_dialog")
        self.setModal(False)
        self.resize(460, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        self.hint = label("", role="muted")
        layout.addWidget(self.hint)
        self.filter = QLineEdit()
        self.filter.setObjectName("search")
        self.filter.textChanged.connect(self.apply_filter)
        layout.addWidget(self.filter)
        self.tree = QTreeWidget()
        self.tree.setRootIsDecorated(False)
        self.tree.setColumnWidth(0, 170)
        self.rows = []          # (item, source texts) so a language switch can redo every text
        for group, rows in GROUPS:
            top = QTreeWidgetItem(["", ""])
            font = top.font(0)
            font.setBold(True)
            top.setFont(0, font)
            top.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tree.addTopLevelItem(top)
            self.rows.append((top, (group, "")))
            for key, what in rows:
                child = QTreeWidgetItem(["", ""])
                top.addChild(child)
                self.rows.append((child, (key, what)))
        self.tree.expandAll()
        layout.addWidget(self.tree, 1)
        self.retranslate()

    def retranslate(self):
        """Every text from its catalog source (tree items are not widgets: translate_tree skips them)."""
        self.setWindowTitle(tr("快捷鍵一覽"))
        self.hint.setText(tr("可以開著一邊寫譜；打字可篩選。"))
        self.filter.setPlaceholderText(tr("搜尋快捷鍵或功能"))
        self.tree.setHeaderLabels([tr("按鍵"), tr("功能")])
        for item, sources in self.rows:
            for column, source in enumerate(sources):
                item.setText(column, tr(source) if source else "")
        self.apply_filter(self.filter.text())

    def apply_filter(self, text):
        needle = text.strip().casefold()
        for index in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(index)
            visible = 0
            for child_index in range(top.childCount()):
                child = top.child(child_index)
                text = child.text(0) + " " + child.text(1)
                match = not needle or needle in text.casefold() or needle in _portable_keys(text).casefold()
                child.setHidden(not match)
                visible += match
            top.setHidden(visible == 0)
