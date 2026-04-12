import logging


LOGGER_ALIASES = {
    "MasterCron": "master",
    "CronIntradayRefresh": "intraday",
    "CronPreTipRefresh": "pretip",
    "CronLineMovement": "intraday",
    "CronClosingLines": "closing",
    "RunPipeline": "pipeline",
    "PipelineStdout": "pipe-out",
    "CronSimulator": "simulator",
    "ClosingLineSkips": "skip-log",
    "EdgeScore": "edge-score",
    "scrapers.fetch_odds_draftkings": "draftkings",
    "scrapers.fetch_odds_fanduel": "fanduel",
    "utils.upsert_props": "props-sync",
    "utils.upsert_market_history": "market-sync",
    "utils.aggregator": "aggregate",
    "utils.supabase_client": "supabase",
}

LEVEL_ALIASES = {
    "CRITICAL": "ERROR",
    "ERROR": "ERROR",
    "WARNING": "WARN",
    "INFO": "INFO",
    "DEBUG": "DEBUG",
}


def _short_logger_name(name: str) -> str:
    if not name:
        return "app"
    alias = LOGGER_ALIASES.get(name)
    if alias:
        return alias
    tail = name.rsplit(".", 1)[-1].replace("_", "-")
    return tail[:12]


def _stringify_field(value) -> str:
    if isinstance(value, float):
        return f"{value:.1f}"
    if isinstance(value, (list, tuple, set)):
        return ",".join(str(item) for item in value)
    return str(value)


def format_fields(**fields) -> str:
    parts = []
    for key, value in fields.items():
        if value is None or value == "":
            continue
        parts.append(f"{key}={_stringify_field(value)}")
    if not parts:
        return ""
    return " | " + " ".join(parts)


def log_section(logger: logging.Logger, title: str, **fields):
    logger.info("========== %s%s ==========", title, format_fields(**fields))


def log_status(logger: logging.Logger, status: str, message: str, level=None, **fields):
    normalized_status = (status or "INFO").upper()
    resolved_level = level
    if resolved_level is None:
        if normalized_status in {"FAIL", "ERROR"}:
            resolved_level = logging.ERROR
        elif normalized_status == "WARN":
            resolved_level = logging.WARNING
        else:
            resolved_level = logging.INFO
    logger.log(resolved_level, "[%s] %s%s", normalized_status, message, format_fields(**fields))


class DashboardLogFormatter(logging.Formatter):
    default_time_format = "%Y-%m-%d %H:%M:%S"
    default_msec_format = None

    def _indent(self, text: str, prefix: str) -> str:
        lines = text.splitlines()
        if len(lines) <= 1:
            return text
        indent = " " * len(prefix)
        return lines[0] + "".join(f"\n{indent}{line}" for line in lines[1:])

    def format(self, record: logging.LogRecord) -> str:
        timestamp = self.formatTime(record, self.datefmt)
        level = LEVEL_ALIASES.get(record.levelname, record.levelname).ljust(5)
        logger_name = _short_logger_name(record.name).ljust(12)
        prefix = f"{timestamp} | {level} | {logger_name} | "
        message = self._indent(record.getMessage(), prefix)
        output = prefix + message

        if record.exc_info:
            exc_text = self._indent(self.formatException(record.exc_info), prefix)
            output += f"\n{' ' * len(prefix)}{exc_text}"
        if record.stack_info:
            stack_text = self._indent(self.formatStack(record.stack_info), prefix)
            output += f"\n{' ' * len(prefix)}{stack_text}"

        return output


def configure_logging(level=logging.INFO):
    formatter = DashboardLogFormatter()
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    if not root_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        handler.setLevel(level)
        root_logger.addHandler(handler)
    else:
        for handler in root_logger.handlers:
            handler.setFormatter(formatter)
            if handler.level == logging.NOTSET:
                handler.setLevel(level)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.ERROR)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)
    logging.getLogger("requests.packages.urllib3").setLevel(logging.ERROR)
    logging.getLogger("requests.packages.urllib3.connectionpool").setLevel(logging.ERROR)
