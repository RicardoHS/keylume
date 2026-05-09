"""CLI interface for Keylume."""
from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import sys
import textwrap
from importlib.resources import files as resource_files
from pathlib import Path

import click

from keylume.config import Config
from keylume.hid import HIDTransport
from keylume.protocol import (
    encode_disable,
    encode_enable,
    encode_ping,
    encode_set_all,
)


@click.group()
@click.option("-c", "--config", "config_path", type=click.Path(exists=True), default=None)
@click.option("-v", "--verbose", is_flag=True, help="Show INFO logs.")
@click.option("-d", "--debug", is_flag=True, help="Show DEBUG logs (all modules).")
@click.pass_context
def cli(ctx, config_path, verbose, debug):
    """Keylume — external LED control for Keychron K8 Pro."""
    if debug:
        level = logging.DEBUG
    elif verbose:
        level = logging.INFO
    else:
        level = logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    ctx.ensure_object(dict)
    path = Path(config_path) if config_path else None
    ctx.obj["config"] = Config(path)


@cli.command()
@click.option("--tray/--no-tray", default=False, help="Show system tray icon.")
@click.pass_context
def start(ctx, tray):
    """Start the keylume daemon (foreground)."""
    import threading

    from keylume.daemon import Daemon

    config = ctx.obj["config"]
    daemon = Daemon(config)

    if tray:
        my_pid = os.getpid()

        def _run_tray():
            try:
                from keylume.tray import run_tray
                run_tray(
                    config,
                    on_quit=lambda: os.kill(my_pid, signal.SIGTERM),
                    on_reload=lambda: os.kill(my_pid, signal.SIGHUP),
                    on_activate=daemon.activate,
                    on_deactivate=daemon.deactivate,
                    on_restart=daemon.restart,
                )
            except Exception:
                logging.getLogger(__name__).exception("Tray failed")

        t = threading.Thread(target=_run_tray, daemon=True)
        t.start()

    daemon.run()


@cli.command()
@click.pass_context
def status(ctx):
    """Ping the keyboard and show status."""
    config = ctx.obj["config"]
    hid = HIDTransport(
        vendor_id=config.hid_vendor_id,
        product_id=config.hid_product_id,
    )
    try:
        hid.open()
        resp = hid.send_and_receive(encode_ping())
        if resp.get("type") == "pong":
            click.echo(f"Keyboard found!")
            click.echo(f"  Version:   {resp['version']}")
            click.echo(f"  Active:    {resp['active']}")
            click.echo(f"  LED count: {resp['led_count']}")
        else:
            click.echo(f"Unexpected response: {resp}")
            sys.exit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    finally:
        hid.close()


@cli.command()
@click.argument("color")
@click.pass_context
def test(ctx, color: str):
    """Set all LEDs to a color. COLOR is r,g,b (e.g. 255,0,0)."""
    parts = color.split(",")
    if len(parts) != 3:
        click.echo("Color must be r,g,b (e.g. 255,0,0)", err=True)
        sys.exit(1)
    r, g, b = int(parts[0]), int(parts[1]), int(parts[2])

    config = ctx.obj["config"]
    hid = HIDTransport(
        vendor_id=config.hid_vendor_id,
        product_id=config.hid_product_id,
    )
    try:
        hid.open()
        resp = hid.send_and_receive(encode_enable(10))
        if resp.get("type") != "ack":
            click.echo(f"Failed to enable: {resp}", err=True)
            sys.exit(1)

        resp = hid.send_and_receive(encode_set_all(r, g, b))
        if resp.get("type") == "ack":
            click.echo(f"All LEDs set to ({r}, {g}, {b})")
            click.echo("Will auto-revert in 10 seconds")
        else:
            click.echo(f"Failed: {resp}", err=True)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    finally:
        hid.close()


@cli.command()
@click.pass_context
def tray(ctx):
    """Launch system tray app for live configuration."""
    from keylume.tray import run_tray

    run_tray(ctx.obj["config"])


def _build_cli_prefix(config: Config) -> list[str]:
    prefix: list[str] = []
    if config.path:
        prefix.extend(["-c", str(config.path.expanduser().resolve())])
    return prefix


def _locate_keylume_executable() -> str | None:
    argv0 = Path(sys.argv[0]).expanduser()
    if argv0.exists():
        return str(argv0.resolve())
    return shutil.which("keylume")


def _desktop_file_content(
    exec_path: str,
    config: Config,
    icon_path: str,
    combined: bool,
) -> str:
    command = ["start", "--tray"] if combined else ["tray"]
    exec_cmd = [exec_path, *_build_cli_prefix(config), *command]
    exec_str = " ".join(_exec_arg(part) for part in exec_cmd)
    return textwrap.dedent(
        f"""\
        [Desktop Entry]
        Type=Application
        Version=1.0
        Name=Keylume
        Comment=Control Keylume from the system tray
        Exec={exec_str}
        Icon={icon_path}
        Terminal=false
        Categories=Utility;Settings;
        StartupNotify=false
        """
    )


def _service_file_content(exec_path: str, config: Config) -> str:
    start_cmd = [exec_path, *_build_cli_prefix(config), "start"]
    stop_cmd = [exec_path, *_build_cli_prefix(config), "off"]
    start_str = " ".join(_exec_arg(part) for part in start_cmd)
    stop_str = " ".join(_exec_arg(part) for part in stop_cmd)
    return textwrap.dedent(
        f"""\
        [Unit]
        Description=Keylume LED control daemon
        After=graphical-session.target
        PartOf=graphical-session.target

        [Service]
        Type=simple
        ExecStart={start_str}
        ExecStop={stop_str}
        Restart=on-failure
        RestartSec=5
        SupplementaryGroups=plugdev input

        [Install]
        WantedBy=default.target
        """
    )


def _exec_arg(value: str) -> str:
    """Quote an argument for desktop entries and systemd unit Exec lines."""
    escaped = value.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


@cli.command("install-desktop")
@click.option("--combined/--split", "combined", default=True, help="Launch the working combined app (`start --tray`) instead of a tray-only frontend.")
@click.option("--autostart/--no-autostart", default=True, help="Install XDG autostart entry.")
@click.option("--service/--no-service", "install_service", default=False, help="Install a separate systemd user service for the daemon.")
@click.option("--enable/--no-enable", "enable_service", default=False, help="Enable and start the systemd user service after installing it.")
@click.pass_context
def install_desktop(ctx, combined: bool, autostart: bool, install_service: bool, enable_service: bool):
    """Install desktop launcher, autostart entry, and user service."""
    config = ctx.obj["config"]
    exec_path = _locate_keylume_executable()
    if not exec_path:
        click.echo("Could not find the 'keylume' executable in PATH.", err=True)
        sys.exit(1)

    if combined and install_service:
        click.echo(
            "The combined launcher already starts the daemon and tray together; "
            "do not combine it with --service.",
            err=True,
        )
        sys.exit(1)

    icon_path = resource_files("keylume.assets").joinpath("keylume.svg")
    applications_dir = Path.home() / ".local" / "share" / "applications"
    desktop_entry_path = applications_dir / "keylume.desktop"
    autostart_path = Path.home() / ".config" / "autostart" / "keylume.desktop"
    service_dir = Path.home() / ".config" / "systemd" / "user"
    service_path = service_dir / "keylume.service"

    applications_dir.mkdir(parents=True, exist_ok=True)
    desktop_entry_path.write_text(
        _desktop_file_content(exec_path, config, str(icon_path), combined=combined),
        encoding="utf-8",
    )
    click.echo(f"Installed desktop launcher: {desktop_entry_path}")

    if autostart:
        autostart_path.parent.mkdir(parents=True, exist_ok=True)
        autostart_path.write_text(desktop_entry_path.read_text(encoding="utf-8"), encoding="utf-8")
        click.echo(f"Installed autostart entry: {autostart_path}")

    if install_service:
        service_dir.mkdir(parents=True, exist_ok=True)
        service_path.write_text(
            _service_file_content(exec_path, config),
            encoding="utf-8",
        )
        click.echo(f"Installed user service: {service_path}")

        try:
            daemon_reload = subprocess.run(
                ["systemctl", "--user", "daemon-reload"],
                capture_output=True,
                text=True,
            )
        except OSError as e:
            click.echo(f"Warning: could not run systemctl --user: {e}", err=True)
            daemon_reload = None
        if daemon_reload is None:
            pass
        elif daemon_reload.returncode != 0:
            click.echo(
                f"Warning: systemctl --user daemon-reload failed: {daemon_reload.stderr.strip()}",
                err=True,
            )
        elif enable_service:
            try:
                enable = subprocess.run(
                    ["systemctl", "--user", "enable", "--now", "keylume.service"],
                    capture_output=True,
                    text=True,
                )
            except OSError as e:
                click.echo(f"Warning: could not enable keylume.service: {e}", err=True)
            else:
                if enable.returncode != 0:
                    click.echo(
                        f"Warning: enabling keylume.service failed: {enable.stderr.strip()}",
                        err=True,
                    )
                else:
                    click.echo("Enabled and started keylume.service")

    click.echo("")
    click.echo("Launcher command:")
    launcher_command = "start --tray" if combined else "tray"
    click.echo(f"  {exec_path} {' '.join(_build_cli_prefix(config))} {launcher_command}".rstrip())


@cli.command()
@click.pass_context
def off(ctx):
    """Disable keylume mode and restore normal RGB."""
    config = ctx.obj["config"]
    hid = HIDTransport(
        vendor_id=config.hid_vendor_id,
        product_id=config.hid_product_id,
    )
    try:
        hid.open()
        resp = hid.send_and_receive(encode_disable())
        if resp.get("type") == "ack":
            click.echo("Keylume mode disabled, normal RGB restored")
        else:
            click.echo(f"Response: {resp}")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    finally:
        hid.close()
