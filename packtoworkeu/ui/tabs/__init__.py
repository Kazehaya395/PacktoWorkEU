"""Per-tab UI builders for PacktoWorkEU (PySide6 restart).

The planned split documented by the Tkinter-era placeholder is now real:
each tab is a self-contained QWidget that receives its dependencies through
constructor arguments (db_path / coordinator / refresh callbacks), so tabs
can be reasoned about and tested in isolation.  ``ui/app.py`` only wires
them together.
"""

from packtoworkeu.ui.tabs.applications import ApplicationsTab
from packtoworkeu.ui.tabs.dashboard import DashboardTab
from packtoworkeu.ui.tabs.data_management import DataManagementTab
from packtoworkeu.ui.tabs.search import SearchTab
from packtoworkeu.ui.tabs.tailoring import TailoringTab
from packtoworkeu.ui.tabs.tools import ToolsTab
from packtoworkeu.ui.tabs.insights import InsightsTab
from packtoworkeu.ui.tabs.assisted_ingestion import AssistedIngestionTab

__all__ = [
    "ApplicationsTab",
    "DashboardTab",
    "DataManagementTab",
    "SearchTab",
    "TailoringTab",
    "ToolsTab",
    "InsightsTab",
    "AssistedIngestionTab",
]
