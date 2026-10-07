import contextvars
import json
import logging
import sys
from datetime import datetime, timezone

_ctx: contextvars.ContextVar[dict] = contextvars.ContextVar("log_ctx", default={})


def bind(**kw) -> None:
    """Attach trace fields (customer_id, payment_id, call_id, ...) to every later log line of this request."""
    _ctx.set({**_ctx.get(), **{k: v for k, v in kw.items() if v is not None}})


def get_ctx() -> dict:
    return _ctx.get()


def reset_ctx() -> None:
    _ctx.set({})


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "level": record.levelname,
               "service": self.service, "msg": record.getMessage(), **get_ctx()}
        extra = getattr(record, "fields", None)
        if extra:
            out.update(extra)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def setup_logging(service: str, level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    logging.getLogger("uvicorn.access").disabled = True  # our middleware logs one structured line instead


def log(logger: logging.Logger, msg: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, msg, extra={"fields": fields})