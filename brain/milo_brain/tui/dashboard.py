"""Main dashboard screen: identity, connection, and model panels."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Static


class IdentityPanel(Static):
    def render_identity(self, name: str, brain_id: str, tier: str, gpu: str) -> None:
        self.update(
            f"[b]Identity[/b]\n"
            f"Name: {name}\n"
            f"ID: {brain_id}\n"
            f"Tier: {tier}\n"
            f"GPU: {gpu or 'cpu'}"
        )


class ConnectionPanel(Static):
    def render_connection(
        self, robot_name: str | None, paired_count: int, last_connected: tuple[str, int] | None,
    ) -> None:
        status = f"connected: {robot_name}" if robot_name else "no robot connected"
        lines = ["[b]Connection[/b]", f"Robot: {status}", f"Paired robots: {paired_count}"]
        if not robot_name and last_connected is not None:
            host, port = last_connected
            lines.append(f"Last seen: {host}:{port}  [dim](r to reconnect)[/dim]")
        lines.append("[dim](c to connect a robot)[/dim]")
        self.update("\n".join(lines))


class ModelPanel(Static):
    def render_model(
        self, llm_model: str, whisper_model: str, piper_voice: str,
        tokens_per_sec_in: float, tokens_per_sec_out: float,
    ) -> None:
        self.update(
            f"[b]Model[/b]\n"
            f"LLM: {llm_model}\n"
            f"Whisper: {whisper_model}\n"
            f"Piper: {piper_voice}\n"
            f"Tokens/s  in: {tokens_per_sec_in:.1f} ^   out: {tokens_per_sec_out:.1f} v\n"
            f"[dim](m to change model)[/dim]"
        )


class DashboardScreen(Screen):
    """The default screen: read-only panels, refreshed by MiloBrainApp's
    periodic timer calling refresh_from() -- not reactive watchers, matching
    milo-dashboard's existing TopBar.update_bar() convention."""

    CSS = """
    DashboardScreen Static {
        border: round $primary;
        padding: 0 1;
        width: 1fr;
        height: auto;
    }
    #credit {
        dock: bottom;
        height: 1;
        content-align: right middle;
        color: $text-muted;
        padding: 0 1;
        border: none;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            with Horizontal():
                yield IdentityPanel(id="identity-panel")
                yield ConnectionPanel(id="connection-panel")
            yield ModelPanel(id="model-panel")
        yield Static("by DAMA", id="credit")
        yield Footer()

    def refresh_from(self, connector, cfg, rate_tracker) -> None:
        robot = connector.connected_robot
        self.query_one(IdentityPanel).render_identity(cfg.name, cfg.brain_id, cfg.tier, cfg.gpu)
        self.query_one(ConnectionPanel).render_connection(
            robot.name if robot else None, len(connector.paired_ids()), connector.last_connected,
        )
        self.query_one(ModelPanel).render_model(
            cfg.llm_model, cfg.whisper_model, cfg.piper_voice,
            rate_tracker.tokens_per_sec_in, rate_tracker.tokens_per_sec_out,
        )
