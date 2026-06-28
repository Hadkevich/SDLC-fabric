"""CV connector: parse a single CV file (.txt, .md, or optionally .pdf).

Supports plain-text (``*.txt``) and Markdown (``*.md``) files out of the
box.  PDF parsing is optional and guarded by a conditional import:

- **PyMuPDF** (``import fitz``): fastest; install with ``pip install pymupdf``.
- **pdfminer.six** (``from pdfminer.high_level import extract_text``): pure
  Python fallback; install with ``pip install pdfminer.six``.

When *neither* PDF library is available:
- :meth:`CVConnector.pdf_supported` returns ``False``.
- A PDF upload yields a degraded :class:`~.base.SourceDocument` with empty
  ``cv_text`` and a note in ``self.errors``.  The connector **never raises**.

The connector processes data entirely in memory (never writes to disk).
"""
from __future__ import annotations

import logging
from typing import Iterator, Optional

from .base import BaseConnector, SourceDocument

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional PDF support — probed once at import time
# ---------------------------------------------------------------------------
_PDF_BACKEND: Optional[str] = None

try:
    import fitz as _fitz  # type: ignore  # PyMuPDF
    _PDF_BACKEND = "pymupdf"
    logger.debug("CVConnector: PyMuPDF (fitz) PDF backend available.")
except ImportError:
    try:
        from pdfminer.high_level import extract_text as _pdfminer_extract  # type: ignore
        _PDF_BACKEND = "pdfminer"
        logger.debug("CVConnector: pdfminer.six PDF backend available.")
    except ImportError:
        logger.debug(
            "CVConnector: no PDF backend found (PyMuPDF and pdfminer.six both "
            "absent); PDF uploads will yield a degraded result."
        )


def _extract_pdf(content: bytes, filename: str) -> tuple[str, Optional[str]]:
    """Extract text from PDF *content*.

    Returns ``(text, error_note)`` where *error_note* is ``None`` on success.
    """
    if _PDF_BACKEND is None:
        return "", (
            "PDF support unavailable: install PyMuPDF "
            "(`pip install pymupdf`) or pdfminer.six "
            "(`pip install pdfminer.six`) to enable PDF parsing."
        )

    try:
        if _PDF_BACKEND == "pymupdf":
            import fitz  # noqa: PLC0415
            with fitz.open(stream=content, filetype="pdf") as doc:
                pages = [page.get_text() for page in doc]
            return "\n".join(pages), None

        if _PDF_BACKEND == "pdfminer":
            import io  # noqa: PLC0415
            from pdfminer.high_level import extract_text  # noqa: PLC0415
            return extract_text(io.BytesIO(content)), None

    except Exception as exc:  # noqa: BLE001
        return "", f"PDF parse error ({filename}): {exc}"

    return "", "Unknown PDF backend state."  # unreachable


class CVConnector(BaseConnector):
    """Parse a single CV file into exactly one :class:`~.base.SourceDocument`.

    Supports ``*.txt`` and ``*.md`` natively.  ``*.pdf`` support requires an
    optional PDF library (PyMuPDF or pdfminer.six).  When the library is
    absent the connector yields a degraded SourceDocument (empty ``cv_text``)
    rather than raising.

    Args:
        content: Raw file bytes.
        filename: Original filename — the extension determines the parsing
            path (``*.txt`` / ``*.md`` → plain text; ``*.pdf`` → PDF path).
        display_name: Optional person's display name to seed the profile.
        email: Optional person's email to seed the profile.
    """

    kind: str = "file"
    availability: str = "live"
    source_name: str = "cv"
    display_name_label: str = "CV / Resume (TXT, MD, PDF)"
    description: str = (
        "Parses a single CV or resume file (.txt/.md, or .pdf when a PDF "
        "library is installed) into cv_text for profile enrichment."
    )
    required_credentials: list = []

    @classmethod
    def pdf_supported(cls) -> bool:
        """Return ``True`` if a PDF parsing library is available at runtime."""
        return _PDF_BACKEND is not None

    @classmethod
    def accepted_file_types_list(cls) -> list[str]:
        """Return the list of accepted file extensions."""
        exts = [".txt", ".md"]
        if cls.pdf_supported():
            exts.append(".pdf")
        return exts

    # Build the ``accepted_file_types`` attribute dynamically so the
    # ConnectorInfo descriptor reflects actual PDF availability.
    @property  # type: ignore[override]
    def accepted_file_types(self) -> list[str]:  # type: ignore[override]
        return self.accepted_file_types_list()

    def __init__(
        self,
        content: bytes,
        filename: str = "cv.txt",
        display_name: str = "",
        email: str = "",
    ) -> None:
        super().__init__()
        self._content = content
        self._filename = filename
        self._display_name = display_name
        self._email = email

    def fetch(self, **kwargs) -> Iterator[SourceDocument]:  # type: ignore[override]
        """Yield exactly one :class:`~.base.SourceDocument` with ``cv_text`` set.

        On any parse error the document's ``cv_text`` will be empty and the
        error note will be recorded in ``self.errors``.  The method never
        raises.
        """
        try:
            yield from self._parse()
        except Exception as exc:  # noqa: BLE001
            msg = f"CVConnector unexpected error processing '{self._filename}': {exc}"
            self.errors.append(msg)
            logger.warning(msg)
            # Still yield a degraded document so the ETL layer counts it
            yield self._make_doc("", self._filename)

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _parse(self) -> Iterator[SourceDocument]:
        # Derive extension from filename (default to 'txt')
        if "." in self._filename:
            ext = self._filename.rsplit(".", 1)[-1].lower()
        else:
            ext = "txt"

        cv_text = ""
        error_note: Optional[str] = None

        if ext in ("txt", "md"):
            cv_text = self._content.decode("utf-8", errors="replace")
        elif ext == "pdf":
            cv_text, error_note = _extract_pdf(self._content, self._filename)
        else:
            # Unknown extension — treat as plain text
            logger.info(
                "CVConnector: unknown extension '.%s' for '%s'; treating as plain text.",
                ext,
                self._filename,
            )
            cv_text = self._content.decode("utf-8", errors="replace")

        if error_note:
            self.errors.append(error_note)
            logger.warning("CVConnector: %s", error_note)

        yield self._make_doc(cv_text.strip(), self._filename)

    def _make_doc(self, cv_text: str, filename: str) -> SourceDocument:
        """Construct the SourceDocument, deriving identity fields from filename."""
        # Strip extension for a clean base name
        base_name = filename.rsplit(".", 1)[0] if "." in filename else filename
        # Remove path separators if present
        base_name = base_name.replace("/", "_").replace("\\", "_")

        external_id = self._email or base_name
        display_name = self._display_name or base_name
        email = self._email or f"{base_name.lower().replace(' ', '.')}@cv.example"

        return SourceDocument(
            external_id=external_id,
            display_name=display_name,
            email=email,
            cv_text=cv_text,
            source="cv",
        )
