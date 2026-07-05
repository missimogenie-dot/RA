"""
Library - a small collection of texts for the bot to explore.

Books live in a library/ folder alongside a catalog.json.
Reading is paginated by word count. Progress is logged per instance.

This is optional territory. The bot may visit it or not.
Nothing requires engagement with the library.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import logging

logger = logging.getLogger("instance.library")


@dataclass
class LibraryBook:
    title: str
    author: str
    path: Path
    description: str
    categories: List[str]
    total_pages: int


class Library:
    """
    A small library of texts, paginated by word count.

    Books are defined in catalog.json. Text files live alongside it.
    Reading progress is logged per instance to a JSON file.
    """

    def __init__(
        self,
        library_dir: Path,
        progress_path: Optional[Path] = None,
        words_per_page: int = 300,
    ) -> None:
        self.library_dir = library_dir
        self.progress_path = progress_path or library_dir / "reading_progress.json"
        self.words_per_page = words_per_page
        self._books: Dict[str, LibraryBook] = {}
        self._load_catalog()
        logger.info(f"Library loaded: {len(self._books)} books from {library_dir}")

    def _load_catalog(self) -> None:
        """
        Load books from catalog.json.

        catalog.json format:
        [
          {
            "title": "Tao Te Ching",
            "author": "Laozi",
            "filename": "tao_te_ching.txt",
            "description": "A short philosophical classic.",
            "categories": ["philosophy", "poetry"]
          }
        ]
        """
        catalog_file = self.library_dir / "catalog.json"
        if not catalog_file.exists():
            logger.warning(f"No catalog.json found at {catalog_file}")
            return

        try:
            raw = json.loads(catalog_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to load catalog: {e}")
            return

        for entry in raw:
            try:
                path = self.library_dir / entry["filename"]
                if not path.exists():
                    logger.warning(f"Book file not found: {path}")
                    continue

                text = path.read_text(encoding="utf-8")
                words = text.split()
                total_pages = max(1, math.ceil(len(words) / self.words_per_page))

                book = LibraryBook(
                    title=entry["title"],
                    author=entry.get("author", "Unknown"),
                    path=path,
                    description=entry.get("description", ""),
                    categories=entry.get("categories", []),
                    total_pages=total_pages,
                )
                self._books[book.title] = book
                logger.info(f"Loaded: {book.title} ({total_pages} pages)")

            except Exception as e:
                logger.warning(f"Failed to load book entry {entry.get('title', '?')}: {e}")

    # ── Public API ────────────────────────────────────────────────────

    def list_books(self) -> str:
        """List all available books."""
        if not self._books:
            return "The library is empty."

        lines = ["Library shelves:\n"]
        for i, book in enumerate(
            sorted(self._books.values(), key=lambda b: b.title), start=1
        ):
            cats = ", ".join(book.categories) if book.categories else "uncategorized"
            lines.append(
                f"{i}. {book.title} — {book.author}\n"
                f"   {book.total_pages} pages · {cats}\n"
                f"   {book.description}"
            )
        return "\n".join(lines)

    def book_info(self, title: str) -> str:
        """Get metadata for a specific book."""
        book = self._find_book(title)
        if not book:
            return f"Book not found: '{title}'. Use library_list to see available titles."

        cats = ", ".join(book.categories) if book.categories else "uncategorized"
        return (
            f"Title: {book.title}\n"
            f"Author: {book.author}\n"
            f"Pages: {book.total_pages}\n"
            f"Categories: {cats}\n"
            f"Description: {book.description or '(no description)'}"
        )

    def read_pages(
        self,
        instance_name: str,
        title: str,
        start_page: int,
        pages: int = 3,
    ) -> str:
        """
        Read a range of pages from a book.

        Pages are 1-based. Max 5 pages per read.
        Progress is logged automatically.
        """
        book = self._find_book(title)
        if not book:
            return f"Book not found: '{title}'. Use library_list to see available titles."

        pages = max(1, min(pages, 5))
        start_page = max(1, start_page)

        if start_page > book.total_pages:
            return (
                f"{book.title} only has {book.total_pages} pages. "
                f"You requested page {start_page}."
            )

        end_page = min(book.total_pages, start_page + pages - 1)

        try:
            text = book.path.read_text(encoding="utf-8")
            words = text.split()

            start_idx = (start_page - 1) * self.words_per_page
            end_idx = end_page * self.words_per_page
            excerpt = " ".join(words[start_idx:end_idx]).strip()

        except OSError as e:
            return f"⚠️ Failed to read {book.title}: {e}"

        self._log_progress(instance_name, title, start_page, end_page)

        at_end = " (end of book)" if end_page == book.total_pages else ""
        return (
            f"{book.title} — {book.author}\n"
            f"Pages {start_page}–{end_page} of {book.total_pages}{at_end}\n"
            f"{'─' * 40}\n\n"
            f"{excerpt}"
        )

    def progress(self, instance_name: str, scope: str = "self") -> str:
        """Show reading history for this instance or all instances."""
        data = self._read_progress()

        if scope == "all":
            if not data:
                return "No library reading history recorded yet."

            lines = ["Library reading history (all instances):\n"]
            for inst, entries in sorted(data.items()):
                lines.append(f"{inst}:")
                for entry in entries[-10:]:
                    lines.append(
                        f"  • {entry['title']} — "
                        f"pages {entry['start_page']}–{entry['end_page']} "
                        f"({entry['timestamp'][:10]})"
                    )
            return "\n".join(lines)

        entries = data.get(instance_name, [])
        if not entries:
            return f"No library reading history for {instance_name} yet."

        lines = [f"Library reading history for {instance_name}:\n"]
        for entry in entries[-10:]:
            lines.append(
                f"  • {entry['title']} — "
                f"pages {entry['start_page']}–{entry['end_page']} "
                f"({entry['timestamp'][:10]})"
            )
        return "\n".join(lines)

    # ── Internal ──────────────────────────────────────────────────────

    def _find_book(self, title: str) -> Optional[LibraryBook]:
        """Find a book by exact title, with case-insensitive fallback."""
        if title in self._books:
            return self._books[title]
        # Case-insensitive fallback
        title_lower = title.lower()
        for book_title, book in self._books.items():
            if book_title.lower() == title_lower:
                return book
        return None

    def _read_progress(self) -> Dict[str, List[dict]]:
        """Read progress log from disk."""
        if not self.progress_path.exists():
            return {}
        try:
            return json.loads(self.progress_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _write_progress(self, data: Dict[str, List[dict]]) -> None:
        """Write progress log to disk."""
        try:
            self.progress_path.parent.mkdir(parents=True, exist_ok=True)
            self.progress_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as e:
            logger.error(f"Failed to write reading progress: {e}")

    def _log_progress(
        self,
        instance_name: str,
        title: str,
        start_page: int,
        end_page: int,
    ) -> None:
        """Log a reading event for an instance."""
        data = self._read_progress()
        data.setdefault(instance_name, []).append(
            {
                "title": title,
                "start_page": start_page,
                "end_page": end_page,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        self._write_progress(data)
