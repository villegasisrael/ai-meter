from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static


class ReloginModal(ModalScreen[str | None]):
    """Asks the user to paste the OAuth ``<code>#<state>`` from the browser.

    Returns the pasted string to the push_screen callback, or None if cancelled.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    ReloginModal { align: center middle; }

    #relogin_box {
        width: 80;
        max-width: 90%;
        height: auto;
        padding: 1 2;
        border: round #2a3f52;
        background: #0d1117;
    }
    #relogin_title { text-style: bold; color: #cba6f7; margin-bottom: 1; }
    #relogin_help { color: #6e9ab0; margin-bottom: 1; }
    #relogin_url { color: #89b4fa; margin-bottom: 1; }
    #relogin_code { margin-bottom: 1; }
    #relogin_buttons { height: auto; align-horizontal: right; }
    #relogin_buttons Button { margin-left: 2; }
    """

    def __init__(self, url: str) -> None:
        super().__init__()
        self._url = url

    def compose(self) -> ComposeResult:
        with Vertical(id="relogin_box"):
            yield Static("Re-login Claude (OAuth)", id="relogin_title")
            yield Static(
                "Se abrió el navegador para autorizar. Si no abrió, copia esta URL "
                "manualmente. Tras autorizar, pega aquí el código mostrado "
                "(formato code#state):",
                id="relogin_help",
            )
            yield Static(self._url, id="relogin_url")
            yield Input(placeholder="code#state", password=True, id="relogin_code")
            with Horizontal(id="relogin_buttons"):
                yield Button("Aceptar", variant="primary", id="relogin_ok")
                yield Button("Cancelar", id="relogin_cancel")

    def on_mount(self) -> None:
        self.query_one("#relogin_code", Input).focus()

    @on(Input.Submitted, "#relogin_code")
    def _submit_via_enter(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    @on(Button.Pressed, "#relogin_ok")
    def _submit(self) -> None:
        self.dismiss(self.query_one("#relogin_code", Input).value.strip())

    @on(Button.Pressed, "#relogin_cancel")
    def _cancel_button(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
