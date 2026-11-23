"""Insights role explorer. All DB access and extraction run off the Qt thread."""
from PySide6.QtCore import Qt,QTimer
from PySide6.QtWidgets import (QGroupBox,QVBoxLayout,QHBoxLayout,QLineEdit,QComboBox,
    QCheckBox,QLabel,QTableWidget,QTableWidgetItem,QHeaderView)
from packtoworkeu.i18n import _
from packtoworkeu.insights.role_skills import RoleIndex
from packtoworkeu.ui.async_load import AsyncLoader


class RoleSkillsBox(QGroupBox):
    def __init__(self,path,scope,parent=None):
        super().__init__(parent);self.path=path;self.scope=scope
        self.loader=AsyncLoader(self,name='Role skills');self.index=RoleIndex();self.result=None
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(350)
        self.timer.timeout.connect(self.launch)
        layout=QVBoxLayout(self);row=QHBoxLayout();layout.addLayout(row)
        self.entry=QLineEdit();self.entry.setMaxLength(160);self.entry.setClearButtonEnabled(True);row.addWidget(self.entry,1)
        self.mode=QComboBox();row.addWidget(self.mode)
        for mode in ('role','wildcard','regex'):self.mode.addItem('',mode)
        self.legacy=QCheckBox();layout.addWidget(self.legacy)
        self.status=QLabel();self.variants=QLabel()
        for label in (self.status,self.variants):label.setTextFormat(Qt.PlainText);label.setWordWrap(True);layout.addWidget(label)
        lists=QHBoxLayout();layout.addLayout(lists);self.tables=[]
        for kind in ('technical_skill','soft_skill'):
            table=QTableWidget(0,1);table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            table.verticalHeader().hide();table.setEditTriggers(QTableWidget.NoEditTriggers);table.verticalHeader().setDefaultSectionSize(26)
            table.setFixedHeight(72)
            self.tables.append(table);lists.addWidget(table)
        self.entry.textChanged.connect(self.schedule);self.mode.currentIndexChanged.connect(self.schedule);self.legacy.toggled.connect(self.schedule)
        self.retranslate()

    def retranslate(self):
        self.setTitle(_('Skills by job title'));self.entry.setPlaceholderText(_('Enter a job title, e.g. Analyst or SDE'))
        for i,text in enumerate((_('Role match'),_('Wildcard'),_('Regex'))):self.mode.setItemText(i,text)
        self.mode.setToolTip(_('Role: words or known aliases. Wildcard: * and ? across the whole title. Regex: search inside titles.'))
        self.legacy.setText(_('Include unverified legacy descriptions (at least 200 characters)'))
        for table,label in zip(self.tables,(_('Technical skills mentioned'),_('Soft skills mentioned'))):table.setHorizontalHeaderLabels([label])
        self.schedule()

    def schedule(self,*args):
        self.timer.stop();self.loader.invalidate();self.result=None
        for table in self.tables:table.setRowCount(0)
        self.variants.clear()
        for table in self.tables:table.setFixedHeight(72)
        if self.entry.text().strip():self.status.setText(_('Analyzing matching postings…'));self.timer.start()
        else:self.status.setText(_('Search active, non-expired postings using the country and source above.'))

    def launch(self):
        q=self.entry.text().strip();mode=self.mode.currentData();legacy=self.legacy.isChecked();country,source=self.scope()
        if not q:return
        self.loader.request(lambda:self.index.analyze(self.path,q,mode,country,source,legacy),self.apply,self.failed)

    def failed(self,error):
        messages={'Regex mode needs the regex package. Use Role match or Wildcard, or install it with the Python interpreter that launches the app.':_('Regex mode needs the regex package. Use Role match or Wildcard, or install it with the Python interpreter that launches the app.'), 'Invalid title pattern.':_('Invalid title pattern.'),'Pattern took too long. Simplify it.':_('Pattern took too long. Simplify it.')}
        self.status.setText(messages.get(str(error),_('Could not analyze saved postings: {reason}').format(reason=error)))

    def apply(self,result):
        self.result=result;r=result
        if not r['matched']:self.status.setText(_('No matching active job titles.'));return
        self.status.setText(_('{matched} matched · {analyzed} analyzed · {excluded} excluded. Available: {proven} capture-verified, {legacy} unverified legacy.').format(**r))
        self.variants.setText(_('Matched titles (up to 12): ')+', '.join(f'{s} ({n})' for s,n in r['titles']))
        if not r['analyzed']:self.status.setText(self.status.text()+' '+_('No eligible descriptions for this selection.'));return
        for kind,table in zip(('technical_skill','soft_skill'),self.tables):
            for term,n in r['ranks'][kind]:
                row=table.rowCount();table.insertRow(row);item=QTableWidgetItem(f"{term} — {n}/{r['analyzed']} ({100*n/r['analyzed']:.1f}%)")
                item.setData(Qt.UserRole,term)
                item.setToolTip('\n\n'.join(f'{title} · {company}\n…{excerpt}…' for title,company,excerpt in r['examples'][term]))
                table.setItem(row,0,item)
        # The Insights page already scrolls: show every ranked row, rather
        # than limiting each list to a small independently scrolling window.
        height=max(t.horizontalHeader().sizeHint().height()+2*t.frameWidth()+sum(t.rowHeight(i) for i in range(t.rowCount()))+4 for t in self.tables)
        for t in self.tables:t.setFixedHeight(max(72,height))
        if not any(r['ranks'].values()):self.status.setText(self.status.text()+' '+_('No technical or soft skills recognized in the analyzed descriptions.'))

    def shutdown(self):
        self.timer.stop();self.loader.close()
