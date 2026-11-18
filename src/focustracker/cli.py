"""Command-line interface: `focustracker <command>`."""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import webbrowser
from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

from . import schedule
from .aw import ActivityWatchError, RawData
from .classify import AMBIGUOUS_CONFIDENCE, Classifier
from .config import (
    Config,
    ConfigError,
    config_dir,
    data_dir,
    example_config_path,
    load_config,
    load_env,
    reports_dir,
    user_config_path,
)
from .fake_data import generate_day
from .insights import daily_insights
from .pipeline import DayResult, analyze_day, local_tz
from .report import render_daily, render_weekly
from .report.render import DayRow, weekly_insights
from .telegram import Telegram, TelegramError, format_summary

ENV_TEMPLATE = Path(__file__).parent / "defaults" / "env.example"


# --- helpers -----------------------------------------------------------------------


def _parse_date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a date (YYYY-MM-DD): {text}") from None


def _week_of(day: date) -> list[date]:
    monday = day - timedelta(days=day.weekday())
    return [monday + timedelta(days=i) for i in range(7)]


class Runner:
    """Shared state for commands: config, classifier and where data comes from."""

    def __init__(self, args: argparse.Namespace):
        self.sample = getattr(args, "sample", False)
        if self.sample:
            config = load_config(example_config_path())
            config.settings = replace(config.settings, timezone="Asia/Yerevan")
        else:
            config = load_config()
        self.config: Config = config
        self.source: Path | None = getattr(args, "source", None)
        use_llm = not getattr(args, "no_llm", False) and not self.sample
        self.classifier = Classifier(config, use_llm=use_llm)

    def raw_for(self, day: date) -> RawData | None:
        if self.source:
            path = self.source / f"{day.isoformat()}.json" if self.source.is_dir() else self.source
            return RawData.from_file(path) if path.exists() else RawData()
        if self.sample:
            return generate_day(day, local_tz(self.config.settings))
        return None  # live ActivityWatch

    def day(self, day: date) -> DayResult:
        return analyze_day(day, self.config, self.raw_for(day), self.classifier)

    def today(self) -> date:
        return datetime.now(local_tz(self.config.settings)).date()


def _write(html: str, name: str, out: Path | None) -> Path:
    path = out or reports_dir() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html)
    return path


def build_daily(runner: Runner, day: date, out: Path | None = None) -> tuple[Path, DayResult]:
    result = runner.day(day)
    return _write(render_daily(result, sample=runner.sample), f"daily-{day}.html", out), result


def build_weekly(runner: Runner, day: date, out: Path | None = None):
    days = [(d, runner.day(d)) for d in _week_of(day) if d <= runner.today() or runner.sample]
    html = render_weekly(days, sample=runner.sample)
    monday = days[0][0]
    return _write(html, f"weekly-{monday}.html", out), days


def send_daily(runner: Runner, day: date) -> None:
    path, result = build_daily(runner, day)
    tg = Telegram.from_env()
    if not result.metrics.stints:
        tg.send_message(format_summary(f"Focus report · {day:%a %d %b}", ["No activity was recorded."]))
        return
    tg.send_message(format_summary(f"Focus report · {day:%a %d %b}", daily_insights(result.metrics)))
    tg.send_document(path, caption=f"Full report for {day:%A %d %B}")


def send_weekly(runner: Runner, monday: date) -> None:
    path, days = build_weekly(runner, monday)
    bullets = weekly_insights([DayRow(d, r.metrics) for d, r in days])
    tg = Telegram.from_env()
    tg.send_message(format_summary(f"Weekly focus report · week of {monday:%d %b}", bullets))
    tg.send_document(path, caption=f"Week of {monday:%d %B}")


# --- commands ----------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    cfg = user_config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    if cfg.exists():
        print(f"Config already exists: {cfg}")
    else:
        shutil.copy(example_config_path(), cfg)
        print(f"Created {cfg} — edit it to add your companies, projects and rules.")
    env = config_dir() / ".env"
    if env.exists():
        print(f"Secrets file already exists: {env}")
    else:
        env.write_text(ENV_TEMPLATE.read_text())
        print(f"Created {env} — add your Telegram bot token, chat ID and (optional) Anthropic key.")
    env.chmod(0o600)
    data_dir().mkdir(parents=True, exist_ok=True)
    print(f"Reports and caches go to {data_dir()}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    runner = Runner(args)
    if args.week:
        anchor = args.date or runner.today()
        path, days = build_weekly(runner, anchor, args.out)
        bullets = weekly_insights([DayRow(d, r.metrics) for d, r in days])
    else:
        day = args.date or runner.today() - timedelta(days=1)
        path, result = build_daily(runner, day, args.out)
        bullets = daily_insights(result.metrics)
        if result.llm_error:
            print(f"Note: {result.llm_error}", file=sys.stderr)
        if result.llm_sent:
            print(f"Sent {result.llm_sent} new titles to the Claude API for classification.")
    for line in bullets:
        print(f"• {line}")
    print(f"\nReport: {path}")
    if args.open:
        webbrowser.open(path.resolve().as_uri())
    if args.send:
        tg = Telegram.from_env()
        title = "Weekly focus report" if args.week else "Focus report"
        tg.send_message(format_summary(title, bullets))
        tg.send_document(path)
        print("Sent to Telegram.")
    return 0


def cmd_unclassified(args: argparse.Namespace) -> int:
    runner = Runner(args)
    end = args.date or runner.today()
    totals: dict[tuple[str, str, str], float] = defaultdict(float)
    guesses: dict[tuple[str, str, str], str] = {}
    for i in range(args.days):
        day = end - timedelta(days=i)
        result = runner.day(day)
        for ev in result.events:
            if ev.source in ("rule", "private"):
                continue
            if ev.source == "llm" and ev.confidence >= AMBIGUOUS_CONFIDENCE:
                continue
            key = (ev.event.app, ev.event.domain, ev.event.title)
            totals[key] += ev.event.duration
            guesses[key] = ev.thread
    if not totals:
        print("Every title is covered by a rule or a confident classification.")
        return 0
    print(f"Top unclassified or ambiguous titles, last {args.days} day(s):\n")
    print(f"{'min':>5}  {'app / site':<24}  {'current guess':<28}  title")
    for (app, domain, title), sec in sorted(totals.items(), key=lambda x: -x[1])[: args.limit]:
        where = domain or app
        print(f"{sec / 60:5.1f}  {where[:24]:<24}  {guesses[(app, domain, title)][:28]:<28}  {title[:80]}")
    print(f"\nAdd rules to {user_config_path()} to cover them.")
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    tg = Telegram.from_env()
    tg.send_message(format_summary("focus-tracker", ["Test message: the bot can reach you. 👋"]))
    print("Test message sent.")
    return 0


def cmd_scheduled(args: argparse.Namespace) -> int:
    runner = Runner(args)
    now = datetime.now(local_tz(runner.config.settings))

    def notify(text: str) -> None:
        try:
            Telegram.from_env().send_message(text)
        except TelegramError as exc:
            print(f"Couldn't notify via Telegram: {exc}", file=sys.stderr)

    log = schedule.run_due(
        now,
        schedule.State(),
        send_daily=lambda d: send_daily(runner, d),
        send_weekly=lambda d: send_weekly(runner, d),
        notify_failure=notify,
    )
    for line in log:
        print(f"{now:%Y-%m-%d %H:%M} {line}")
    return 0


def cmd_install_schedule(args: argparse.Namespace) -> int:
    if args.uninstall:
        schedule.uninstall()
        print("Schedule removed.")
    else:
        path = schedule.install()
        print(f"Installed {path}: daily report at 11:00, weekly report on Sunday 21:00.")
    return 0


def cmd_sample(args: argparse.Namespace) -> int:
    tz = local_tz(replace(load_config(example_config_path()).settings, timezone="Asia/Yerevan"))
    args.out.mkdir(parents=True, exist_ok=True)
    for i in range(args.days):
        day = args.start + timedelta(days=i)
        path = args.out / f"{day.isoformat()}.json"
        path.write_text(generate_day(day, tz).to_json())
        print(f"Wrote {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    load_env()
    parser = argparse.ArgumentParser(prog="focustracker", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create config and .env from the examples").set_defaults(func=cmd_init)

    def data_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--date", type=_parse_date, help="YYYY-MM-DD")
        p.add_argument("--no-llm", action="store_true", help="rules only; send nothing to the Claude API")
        p.add_argument("--sample", action="store_true", help="use synthetic data and the example config")
        p.add_argument("--source", type=Path, help="raw ActivityWatch JSON file or folder of <date>.json")

    p = sub.add_parser("report", help="build a daily (default: yesterday) or weekly report")
    data_args(p)
    p.add_argument("--week", action="store_true", help="weekly report for the week containing --date")
    p.add_argument("--out", type=Path, help="output HTML path")
    p.add_argument("--open", action="store_true", help="open the report in the browser")
    p.add_argument("--send", action="store_true", help="also send it via Telegram")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("unclassified", help="list titles no rule covers, to turn into rules")
    data_args(p)
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(func=cmd_unclassified)

    p = sub.add_parser("send", help="Telegram delivery")
    p.add_argument("--test", action="store_true", required=True, help="send a test message")
    p.set_defaults(func=cmd_send)

    sub.add_parser("scheduled", help="send whatever report is due (run by launchd)").set_defaults(
        func=cmd_scheduled
    )

    p = sub.add_parser("install-schedule", help="install the launchd job (11:00 daily, Sun 21:00)")
    p.add_argument("--uninstall", action="store_true")
    p.set_defaults(func=cmd_install_schedule)

    p = sub.add_parser("sample", help="write synthetic raw ActivityWatch days as JSON")
    p.add_argument("--start", type=_parse_date, default=date(2026, 11, 9))
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--out", type=Path, default=Path("sample_data"))
    p.set_defaults(func=cmd_sample)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, ActivityWatchError, TelegramError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
