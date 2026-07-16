"""Isolated browser and PDF executor for Codex-Rosetta ``web.run``."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import secrets
import socket
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit

import httpx
import pymupdf
import pytesseract
from fastapi import FastAPI, HTTPException, Request
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field
from patchright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from bing_search import (
    BingSearchError,
    execute_bing_browser_search,
    execute_bing_search,
)
from google_search import GoogleSearchError, execute_google_search

_SESSION_RE = re.compile(r"[a-f0-9]{64}")
_PAGE_REFERENCE_RE = re.compile(r"turn[0-9]+fetch[0-9]+")
_PDF_REFERENCE_RE = re.compile(r"turn[0-9]+view[0-9]+")
_MAX_REQUEST_BYTES = 64 * 1024
_MAX_RENDERED_LINES = 400
_LINE_WINDOW = 200
_MAX_LINE_CHARS = 2_000
_MAX_LINKS = 200
_MAX_PDF_BYTES = 20 * 1024 * 1024
_MAX_PDF_BYTES_PER_SESSION = 40 * 1024 * 1024
_MAX_PDF_PAGES = 500
_MAX_PDF_TEXT_CHARS = 100_000
_MAX_SESSIONS = 16
_MAX_REFERENCES_PER_SESSION = 16
_SESSION_TTL_SECONDS = 15 * 60
_NAVIGATION_TIMEOUT_MS = 30_000
_ACTION_TIMEOUT_MS = 15_000
_USER_AGENT = "Codex-Rosetta/1.0 (+web-run sidecar)"


class ExecuteRequest(BaseModel):
    """One authenticated sidecar operation."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=64, max_length=64)
    operation: Literal["open", "click", "find", "screenshot"]
    arguments: dict[str, Any]


class SearchRequest(BaseModel):
    """One authenticated self-hosted search request."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal[
        "self_hosted_google",
        "self_hosted_bing",
        "self_hosted_bing_browser",
    ]
    query: str = Field(min_length=1, max_length=4_000)
    max_results: int = Field(default=5, ge=1, le=10)
    include_domains: list[str] = Field(default_factory=list, max_length=20)


class BrowserOperationError(RuntimeError):
    """Stable client-visible browser operation failure."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class PDFDocument:
    """Bounded PDF bytes retained within one browser session."""

    url: str
    content: bytes


@dataclass
class SessionState:
    """Isolated Patchright context and bounded references for one Codex search ID."""

    context: BrowserContext
    pages: OrderedDict[str, Page] = field(default_factory=OrderedDict)
    pdfs: OrderedDict[str, PDFDocument] = field(default_factory=OrderedDict)
    next_turn: int = 0
    last_access: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class WebRunService:
    """Own Patchright and execute scoped navigation plus PDF rendering."""

    def __init__(self) -> None:
        self._patchright: Playwright | None = None
        self._browser: Browser | None = None
        self._sessions: OrderedDict[str, SessionState] = OrderedDict()
        self._sessions_lock = asyncio.Lock()
        self._search_semaphore = asyncio.Semaphore(2)

    async def start(self) -> None:
        self._patchright = await async_playwright().start()
        self._browser = await self._patchright.chromium.launch(
            headless=False,
            chromium_sandbox=True,
            args=["--disable-dev-shm-usage"],
        )

    async def close(self) -> None:
        async with self._sessions_lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            await session.context.close()
        if self._browser is not None:
            await self._browser.close()
        if self._patchright is not None:
            await self._patchright.stop()

    async def execute(self, request: ExecuteRequest) -> str:
        if _SESSION_RE.fullmatch(request.session_id) is None:
            raise BrowserOperationError("Invalid sidecar session_id")
        session = await self._session(request.session_id)
        async with session.lock:
            session.last_access = time.monotonic()
            if request.operation == "open":
                return await self._open(session, request.arguments)
            if request.operation == "click":
                return await self._click(session, request.arguments)
            if request.operation == "find":
                return await self._find(session, request.arguments)
            return await self._screenshot(session, request.arguments)

    async def search(self, request: SearchRequest) -> dict[str, Any]:
        """Execute one stateless search in an isolated context."""
        if self._browser is None:
            raise BrowserOperationError("Browser is not ready", status_code=503)
        async with self._search_semaphore:
            try:
                executors = {
                    "self_hosted_google": execute_google_search,
                    "self_hosted_bing": execute_bing_search,
                    "self_hosted_bing_browser": execute_bing_browser_search,
                }
                executor = executors[request.provider]
                return await executor(
                    self._browser,
                    query=request.query,
                    max_results=request.max_results,
                    include_domains=tuple(request.include_domains),
                    route_handler=self._route_public_requests,
                )
            except ValueError as exc:
                raise BrowserOperationError(str(exc)) from exc
            except (GoogleSearchError, BingSearchError) as exc:
                raise BrowserOperationError(str(exc), status_code=502) from exc

    async def _session(self, session_id: str) -> SessionState:
        async with self._sessions_lock:
            await self._remove_expired_sessions()
            existing = self._sessions.get(session_id)
            if existing is not None:
                self._sessions.move_to_end(session_id)
                return existing
            while len(self._sessions) >= _MAX_SESSIONS:
                _old_id, old = self._sessions.popitem(last=False)
                await old.context.close()
            if self._browser is None:
                raise BrowserOperationError("Browser is not ready", status_code=503)
            context = await self._browser.new_context(
                accept_downloads=False,
                service_workers="block",
            )
            context.set_default_timeout(_ACTION_TIMEOUT_MS)
            context.set_default_navigation_timeout(_NAVIGATION_TIMEOUT_MS)
            await context.route("**/*", self._route_public_requests)
            state = SessionState(context=context)
            self._sessions[session_id] = state
            return state

    async def _remove_expired_sessions(self) -> None:
        cutoff = time.monotonic() - _SESSION_TTL_SECONDS
        expired = [
            session_id
            for session_id, state in self._sessions.items()
            if state.last_access < cutoff
        ]
        for session_id in expired:
            state = self._sessions.pop(session_id)
            await state.context.close()

    async def _route_public_requests(self, route: Any, request: Any) -> None:
        scheme = urlsplit(request.url).scheme.lower()
        if scheme in {"data", "blob", "about"}:
            await route.continue_()
            return
        try:
            await _validate_public_url(request.url)
        except BrowserOperationError:
            await route.abort("blockedbyclient")
            return
        await route.continue_()

    async def _open(self, session: SessionState, arguments: dict[str, Any]) -> str:
        ref_id = _required_string(arguments, "ref_id")
        lineno = _optional_non_negative_int(arguments, "lineno")
        if _PAGE_REFERENCE_RE.fullmatch(ref_id):
            page = _referenced_page(session, ref_id)
            return await _format_page(page, ref_id=ref_id, lineno=lineno)
        if _PDF_REFERENCE_RE.fullmatch(ref_id):
            document = _referenced_pdf(session, ref_id)
            return await _format_pdf_open(document, ref_id=ref_id, lineno=lineno)

        await _validate_public_url(ref_id)
        if _looks_like_pdf(ref_id):
            document = await _download_pdf(ref_id)
            reference = await self._store_pdf(session, document)
            return await _format_pdf_open(document, ref_id=reference, lineno=lineno)

        page = await session.context.new_page()
        try:
            response = await page.goto(ref_id, wait_until="domcontentloaded")
            final_url = page.url
            await _validate_public_url(final_url)
            content_type = response.headers.get("content-type", "") if response else ""
            if "application/pdf" in content_type.lower():
                await page.close()
                document = await _download_pdf(final_url)
                reference = await self._store_pdf(session, document)
                return await _format_pdf_open(document, ref_id=reference, lineno=lineno)
            await _settle_page(page)
            reference = await self._store_page(session, page)
            return await _format_page(page, ref_id=reference, lineno=lineno)
        except BrowserOperationError:
            await page.close()
            raise
        except Exception as exc:
            await page.close()
            if "download is starting" in str(exc).lower():
                document = await _download_pdf(ref_id)
                reference = await self._store_pdf(session, document)
                return await _format_pdf_open(document, ref_id=reference, lineno=lineno)
            raise BrowserOperationError(
                f"Browser open failed: {_bounded_error(exc)}", status_code=502
            ) from exc

    async def _click(self, session: SessionState, arguments: dict[str, Any]) -> str:
        ref_id = _required_string(arguments, "ref_id")
        link_id = _required_non_negative_int(arguments, "id")
        page = _referenced_page(session, ref_id)
        selector = f'[data-rosetta-link-id="{link_id}"]'
        locator = page.locator(selector)
        if await locator.count() != 1:
            raise BrowserOperationError(
                f"Unknown link id {link_id} in {ref_id}", status_code=404
            )
        pages_before = set(session.context.pages)
        try:
            await locator.click()
            await _settle_page(page)
        except Exception as exc:
            raise BrowserOperationError(
                f"Browser click failed: {_bounded_error(exc)}", status_code=502
            ) from exc
        new_pages = [
            candidate
            for candidate in session.context.pages
            if candidate not in pages_before
        ]
        target = new_pages[-1] if new_pages else page
        await _settle_page(target)
        await _validate_public_url(target.url)
        reference = await self._store_page(session, target)
        return await _format_page(target, ref_id=reference, lineno=None)

    async def _find(self, session: SessionState, arguments: dict[str, Any]) -> str:
        ref_id = _required_string(arguments, "ref_id")
        pattern = _required_string(arguments, "pattern")
        if len(pattern) > 1_000:
            raise BrowserOperationError("find.pattern exceeds 1000 characters")
        if _PDF_REFERENCE_RE.fullmatch(ref_id):
            return await _find_in_pdf(_referenced_pdf(session, ref_id), ref_id, pattern)
        if not _PAGE_REFERENCE_RE.fullmatch(ref_id):
            opened = await self._open(session, {"ref_id": ref_id})
            match = re.search(r"Reference: (turn[0-9]+(?:fetch|view)[0-9]+)", opened)
            if match is None:
                raise BrowserOperationError(
                    "Browser did not return a page reference", status_code=502
                )
            ref_id = match.group(1)
            if _PDF_REFERENCE_RE.fullmatch(ref_id):
                return await _find_in_pdf(
                    _referenced_pdf(session, ref_id), ref_id, pattern
                )
        page = _referenced_page(session, ref_id)
        lines = await _page_lines(page)
        return _format_find_results(ref_id, pattern, lines)

    async def _screenshot(
        self, session: SessionState, arguments: dict[str, Any]
    ) -> str:
        ref_id = _required_string(arguments, "ref_id")
        pageno = _required_non_negative_int(arguments, "pageno")
        if _PDF_REFERENCE_RE.fullmatch(ref_id):
            document = _referenced_pdf(session, ref_id)
        else:
            await _validate_public_url(ref_id)
            document = await _download_pdf(ref_id)
            ref_id = await self._store_pdf(session, document)
        return await asyncio.to_thread(_render_pdf_page, document, ref_id, pageno)

    async def _store_page(self, session: SessionState, page: Page) -> str:
        for existing_ref, existing_page in tuple(session.pages.items()):
            if existing_page is page:
                del session.pages[existing_ref]
        reference = f"turn{session.next_turn}fetch0"
        session.next_turn += 1
        session.pages[reference] = page
        await self._trim_references(session)
        return reference

    async def _store_pdf(self, session: SessionState, document: PDFDocument) -> str:
        reference = f"turn{session.next_turn}view0"
        session.next_turn += 1
        session.pdfs[reference] = document
        while sum(len(item.content) for item in session.pdfs.values()) > (
            _MAX_PDF_BYTES_PER_SESSION
        ):
            session.pdfs.popitem(last=False)
        await self._trim_references(session)
        return reference

    async def _trim_references(self, session: SessionState) -> None:
        while len(session.pages) + len(session.pdfs) > _MAX_REFERENCES_PER_SESSION:
            if session.pages:
                _reference, page = session.pages.popitem(last=False)
                await page.close()
            else:
                session.pdfs.popitem(last=False)


async def _settle_page(page: Page) -> None:
    try:
        await page.wait_for_load_state("networkidle", timeout=3_000)
    except Exception:
        pass


async def _format_page(page: Page, *, ref_id: str, lineno: int | None) -> str:
    links = await page.locator("a[href]").evaluate_all(
        """
        elements => {
          const visible = elements.filter(element => {
            const style = getComputedStyle(element);
            const rect = element.getBoundingClientRect();
            return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
          }).slice(0, 200);
          return visible.map((element, index) => {
            const id = index + 1;
            element.dataset.rosettaLinkId = String(id);
            return {id, text: (element.innerText || element.textContent || '').trim(), href: element.href};
          });
        }
        """
    )
    lines = await _page_lines(page)
    start = lineno or 0
    if lines and start >= len(lines):
        raise BrowserOperationError(
            f"open.lineno {start} exceeds page line count {len(lines)}"
        )
    end = min(len(lines), start + _LINE_WINDOW)
    title = await page.title()
    output = [f"Opened URL: {page.url}", f"Reference: {ref_id}"]
    if title.strip():
        output.append(f"Title: {_trim(title.strip(), 500)}")
    output.append(f"Lines {start}-{max(start, end - 1)} of {len(lines)}:")
    output.extend(f"L{index}: {lines[index]}" for index in range(start, end))
    if links:
        output.append("Links:")
        for link in links[:_MAX_LINKS]:
            text = _trim(str(link.get("text") or link.get("href") or "Untitled"), 500)
            href = _trim(str(link.get("href") or ""), 2_000)
            output.append(f"[{link['id']}] {text} -> {href}")
    return "\n".join(output)


async def _page_lines(page: Page) -> tuple[str, ...]:
    text = await page.locator("body").inner_text(timeout=_ACTION_TIMEOUT_MS)
    return _normalize_lines(text)


def _normalize_lines(text: str) -> tuple[str, ...]:
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if line:
            lines.append(_trim(line, _MAX_LINE_CHARS))
        if len(lines) >= _MAX_RENDERED_LINES:
            break
    return tuple(lines)


def _format_find_results(ref_id: str, pattern: str, lines: tuple[str, ...]) -> str:
    needle = pattern.casefold()
    matches = [
        (index, line) for index, line in enumerate(lines) if needle in line.casefold()
    ]
    output = [f"Find in {ref_id}: {pattern}", f"Matches: {len(matches)}"]
    output.extend(f"L{index}: {line}" for index, line in matches[:50])
    if len(matches) > 50:
        output.append(f"... {len(matches) - 50} additional matches omitted")
    return "\n".join(output)


async def _format_pdf_open(
    document: PDFDocument, *, ref_id: str, lineno: int | None
) -> str:
    return await asyncio.to_thread(_format_pdf_open_sync, document, ref_id, lineno)


def _format_pdf_open_sync(
    document: PDFDocument, ref_id: str, lineno: int | None
) -> str:
    with pymupdf.open(stream=document.content, filetype="pdf") as pdf:
        if len(pdf) > _MAX_PDF_PAGES:
            raise BrowserOperationError(f"PDF exceeds {_MAX_PDF_PAGES} pages")
        text = "\n".join(page.get_text("text") for page in pdf)
        lines = _normalize_lines(text[:_MAX_PDF_TEXT_CHARS])
        start = lineno or 0
        if lines and start >= len(lines):
            raise BrowserOperationError(
                f"open.lineno {start} exceeds PDF line count {len(lines)}"
            )
        end = min(len(lines), start + _LINE_WINDOW)
        output = [
            f"Opened PDF: {document.url}",
            f"Reference: {ref_id}",
            f"Pages: {len(pdf)}",
            f"Lines {start}-{max(start, end - 1)} of {len(lines)}:",
        ]
        output.extend(f"L{index}: {lines[index]}" for index in range(start, end))
        if not lines:
            output.append("No embedded text; use screenshot on a page to run OCR.")
        return "\n".join(output)


async def _find_in_pdf(document: PDFDocument, ref_id: str, pattern: str) -> str:
    def find() -> str:
        with pymupdf.open(stream=document.content, filetype="pdf") as pdf:
            lines = _normalize_lines("\n".join(page.get_text("text") for page in pdf))
            return _format_find_results(ref_id, pattern, lines)

    return await asyncio.to_thread(find)


def _render_pdf_page(document: PDFDocument, ref_id: str, pageno: int) -> str:
    with pymupdf.open(stream=document.content, filetype="pdf") as pdf:
        if pageno >= len(pdf):
            raise BrowserOperationError(
                f"screenshot.pageno {pageno} exceeds PDF page count {len(pdf)}"
            )
        page = pdf[pageno]
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
        embedded_text = page.get_text("text").strip()
        text_source = "embedded text"
        if embedded_text:
            text = embedded_text
        else:
            image = Image.open(BytesIO(pixmap.tobytes("png")))
            text = pytesseract.image_to_string(image).strip()
            text_source = "Tesseract OCR"
        lines = _normalize_lines(text)
        output = [
            f"PDF screenshot: {ref_id}",
            f"Source URL: {document.url}",
            f"Page: {pageno} of {len(pdf) - 1}",
            f"Rendered size: {pixmap.width}x{pixmap.height}",
            f"Text source: {text_source}",
        ]
        output.extend(
            f"L{index}: {line}" for index, line in enumerate(lines[:_LINE_WINDOW])
        )
        if not lines:
            output.append("No text was recognized on this rendered PDF page.")
        return "\n".join(output)


async def _download_pdf(url: str) -> PDFDocument:
    current = url
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0),
        follow_redirects=False,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/pdf"},
    ) as client:
        for _redirect in range(4):
            await _validate_public_url(current)
            async with client.stream("GET", current) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise BrowserOperationError(
                            "PDF redirect is missing Location", status_code=502
                        )
                    current = urljoin(current, location)
                    continue
                if response.status_code >= 400:
                    raise BrowserOperationError(
                        f"PDF download returned HTTP {response.status_code}",
                        status_code=502,
                    )
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > _MAX_PDF_BYTES:
                        raise BrowserOperationError(
                            f"PDF exceeds {_MAX_PDF_BYTES} bytes"
                        )
                    chunks.append(chunk)
                content = b"".join(chunks)
                content_type = response.headers.get("content-type", "").lower()
                if "application/pdf" not in content_type and not content.startswith(
                    b"%PDF"
                ):
                    raise BrowserOperationError("Requested resource is not a PDF")
                return PDFDocument(url=str(response.url), content=content)
    raise BrowserOperationError("PDF exceeds 3 redirects", status_code=502)


async def _validate_public_url(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise BrowserOperationError(
            "Only public HTTP(S) URLs without credentials are allowed"
        )
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    try:
        addresses = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )
    except OSError as exc:
        raise BrowserOperationError(
            f"Could not resolve URL host: {_bounded_error(exc)}", status_code=502
        ) from exc
    if not addresses:
        raise BrowserOperationError("URL host did not resolve", status_code=502)
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise BrowserOperationError("URL resolves to a non-public address")


def _looks_like_pdf(url: str) -> bool:
    return urlsplit(url).path.lower().endswith(".pdf")


def _referenced_page(session: SessionState, ref_id: str) -> Page:
    page = session.pages.get(ref_id)
    if page is None:
        raise BrowserOperationError(
            f"Unknown or expired page reference: {ref_id}", status_code=404
        )
    session.pages.move_to_end(ref_id)
    return page


def _referenced_pdf(session: SessionState, ref_id: str) -> PDFDocument:
    document = session.pdfs.get(ref_id)
    if document is None:
        raise BrowserOperationError(
            f"Unknown or expired PDF reference: {ref_id}", status_code=404
        )
    session.pdfs.move_to_end(ref_id)
    return document


def _required_string(arguments: dict[str, Any], field_name: str) -> str:
    value = arguments.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise BrowserOperationError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_non_negative_int(
    arguments: dict[str, Any], field_name: str
) -> int | None:
    value = arguments.get(field_name)
    if value is None:
        return None
    return _validate_non_negative_int(value, field_name)


def _required_non_negative_int(arguments: dict[str, Any], field_name: str) -> int:
    return _validate_non_negative_int(arguments.get(field_name), field_name)


def _validate_non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BrowserOperationError(f"{field_name} must be a non-negative integer")
    return value


def _trim(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _bounded_error(exc: BaseException) -> str:
    return _trim(str(exc).replace("\n", " "), 500)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    token = os.environ.get("WEB_RUN_TOKEN", "").strip()
    if len(token) < 24:
        raise RuntimeError("WEB_RUN_TOKEN must contain at least 24 characters")
    service = WebRunService()
    await service.start()
    app.state.token = token
    app.state.service = service
    try:
        yield
    finally:
        await service.close()


app = FastAPI(lifespan=_lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def _protect_requests(request: Request, call_next: Any):
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > _MAX_REQUEST_BYTES:
                return _error_response(413, "Request body is too large")
        except ValueError:
            return _error_response(400, "Invalid Content-Length")
    if request.url.path != "/health":
        expected = f"Bearer {request.app.state.token}"
        if not secrets.compare_digest(
            request.headers.get("authorization", ""), expected
        ):
            return _error_response(401, "Invalid sidecar bearer token")
    return await call_next(request)


@app.get("/health")
async def _health(request: Request) -> dict[str, Any]:
    service: WebRunService = request.app.state.service
    return {"status": "ok", "browser_ready": service._browser is not None}


@app.post("/v1/execute")
async def _execute(payload: ExecuteRequest, request: Request) -> dict[str, str]:
    try:
        output = await request.app.state.service.execute(payload)
    except BrowserOperationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {"output": output}


@app.post("/v1/search")
async def _search(payload: SearchRequest, request: Request) -> dict[str, Any]:
    try:
        return await request.app.state.service.search(payload)
    except BrowserOperationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _error_response(status_code: int, message: str):
    from fastapi.responses import JSONResponse

    return JSONResponse({"detail": message}, status_code=status_code)
