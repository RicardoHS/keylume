"""CLI interface for Keylume."""
from __future__ import annotations

import logging
import os
import signal
import sys
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
