"""Streaming multipart/form-data parsing.

FastAPI's ``File(...)`` parameters read (and spool to the system temp dir in
plaintext) the whole request body before the handler runs, i.e. before we can
check who is asking. Upload endpoints instead call ``parse_form`` after
authentication: file parts are streamed straight into encrypted ``Spool``
files and the whole body is size-limited.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from python_multipart.multipart import MultipartParser, parse_options_header
from starlette.requests import Request

from .spool import Spool, SpoolFull

MAX_FIELD = 64 * 1024
MAX_PARTS = 500  # each file part is an open scratch file; the web UI sends big folders in batches of 200


class FormError(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status, self.msg = status, msg


@dataclass
class UploadPart:
    field: str
    filename: str
    content_type: str
    spool: Spool

    @property
    def size(self) -> int:
        return self.spool.size


@dataclass
class Form:
    fields: dict[str, list[str]] = field(default_factory=dict)
    files: list[UploadPart] = field(default_factory=list)

    def get(self, name: str, default: str | None = None) -> str | None:
        v = self.fields.get(name)
        return v[0] if v else default

    def file(self, name: str) -> UploadPart | None:
        for f in self.files:
            if f.field == name:
                return f
        return None

    def files_named(self, name: str) -> list[UploadPart]:
        return [f for f in self.files if f.field == name]

    def close(self) -> None:
        for f in self.files:
            f.spool.close()


async def parse_form(request: Request, tmp_dir, max_total: int) -> Form:
    ctype, params = parse_options_header(request.headers.get("content-type", ""))
    if ctype != b"multipart/form-data" or b"boundary" not in params:
        raise FormError(400, "Expected multipart/form-data")
    clen = request.headers.get("content-length")
    if clen and clen.isdigit() and int(clen) > max_total + 1024 * 1024:
        raise FormError(413, f"Upload too large (limit {max_total // (1024 * 1024)} MB)")

    form = Form()
    st = {"hname": b"", "hval": b"", "headers": {}, "cur": None, "buf": bytearray(), "parts": 0, "total": 0}

    def on_part_begin():
        st["headers"] = {}
        st["cur"] = None
        st["buf"] = bytearray()
        st["parts"] += 1
        if st["parts"] > MAX_PARTS:
            raise FormError(400, "Too many form parts")

    def on_header_field(data, start, end):
        st["hname"] += data[start:end]

    def on_header_value(data, start, end):
        st["hval"] += data[start:end]

    def on_header_end():
        st["headers"][st["hname"].lower()] = st["hval"]
        st["hname"], st["hval"] = b"", b""

    def on_headers_finished():
        _, opts = parse_options_header(st["headers"].get(b"content-disposition", b""))
        name = opts.get(b"name", b"").decode("utf-8", "replace")
        if b"filename" in opts:
            fname = opts[b"filename"].decode("utf-8", "replace")
            part = UploadPart(name, fname, st["headers"].get(b"content-type", b"").decode("latin-1"), Spool(tmp_dir, limit=max_total))
            form.files.append(part)
            st["cur"] = part
        else:
            st["cur"] = name

    def on_part_data(data, start, end):
        n = end - start
        st["total"] += n
        if st["total"] > max_total:
            raise FormError(413, f"Upload too large (limit {max_total // (1024 * 1024)} MB)")
        cur = st["cur"]
        if isinstance(cur, UploadPart):
            try:
                cur.spool.write(bytes(data[start:end]))
            except SpoolFull:
                raise FormError(413, f"Upload too large (limit {max_total // (1024 * 1024)} MB)")
        else:
            st["buf"] += data[start:end]
            if len(st["buf"]) > MAX_FIELD:
                raise FormError(413, "Form field too large")

    def on_part_end():
        cur = st["cur"]
        if isinstance(cur, UploadPart):
            cur.spool.finish()
        elif cur is not None:
            form.fields.setdefault(cur, []).append(st["buf"].decode("utf-8", "replace"))

    parser = MultipartParser(
        params[b"boundary"],
        {
            "on_part_begin": on_part_begin,
            "on_part_data": on_part_data,
            "on_part_end": on_part_end,
            "on_header_field": on_header_field,
            "on_header_value": on_header_value,
            "on_header_end": on_header_end,
            "on_headers_finished": on_headers_finished,
        },
    )
    try:
        async for chunk in request.stream():
            parser.write(chunk)
        parser.finalize()
    except FormError:
        form.close()
        raise
    except Exception as exc:
        form.close()
        raise FormError(400, f"Malformed upload: {exc}")
    return form
