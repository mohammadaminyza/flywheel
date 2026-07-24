from pathlib import Path

from textual.app import App

from flywheel.config import Settings, load_settings
from flywheel.storage import Ledger
from flywheel.tui.dashboard import DashboardScreen
from flywheel.tui.wizard import WizardScreen


class FlywheelApp(App[None]):
    CSS_PATH = Path(__file__).with_name("styles.tcss")
    TITLE = "Flywheel"

    def __init__(self, force_wizard: bool = False, settings: Settings | None = None) -> None:
        super().__init__()
        self.settings = settings or load_settings()
        self.ledger = Ledger(self.settings.database_path)
        self.force_wizard = force_wizard

    def on_mount(self) -> None:
        self.ledger.recover_stale()
        if self.force_wizard or not self.settings.setup_completed:
            self.push_screen(WizardScreen(self.settings), self._after_wizard)
        else:
            self.push_screen(DashboardScreen(self.settings, self.ledger))

    def _after_wizard(self, completed: bool | None) -> None:
        if not self.is_screen_installed("dashboard"):
            self.install_screen(DashboardScreen(self.settings, self.ledger), "dashboard")
        self.push_screen("dashboard")

    def action_open_wizard(self) -> None:
        self.push_screen(WizardScreen(self.settings), self._after_wizard)

    def on_unmount(self) -> None:
        self.ledger.close()
