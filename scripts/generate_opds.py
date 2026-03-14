import os
import io
import tempfile
import zipfile
from datetime import datetime
from urllib.parse import quote
import epub_meta
from xml.etree import ElementTree as ET
from xml.etree.ElementTree import Element, SubElement, tostring

# CONFIGURATION
BASE_URL = "https://xlynx555.github.io/opds"
BOOKS_DIR = "books"
OUTPUT_FILE = "index.xml"

MIME_TYPES = {
    ".epub": "application/epub+zip",
    ".fb2": "application/fb2+xml",
    ".pdf": "application/pdf",
    ".mobi": "application/x-mobipocket-ebook",
    ".azw": "application/vnd.amazon.ebook",
    ".azw3": "application/vnd.amazon.mobi8-ebook",
    ".txt": "text/plain",
    ".zip": "application/zip",
}

ZIP_METADATA_CANDIDATES = [".epub", ".fb2", ".pdf", ".mobi", ".azw3", ".azw", ".txt"]


def _fallback_metadata(filename):
    name = os.path.splitext(os.path.basename(filename))[0]
    name = name.replace("_", " ").replace("-", " ").strip()
    return {
        "title": name or "Unknown Title",
        "authors": ["Unknown Author"],
    }


def _normalize_metadata(metadata, fallback_name):
    fallback = _fallback_metadata(fallback_name)
    title = metadata.get("title", "") if isinstance(metadata, dict) else ""
    authors = metadata.get("authors", []) if isinstance(metadata, dict) else []

    if isinstance(authors, str):
        authors = [authors]

    normalized_authors = [a.strip() for a in authors if isinstance(a, str) and a.strip()]
    return {
        "title": title.strip() if isinstance(title, str) and title.strip() else fallback["title"],
        "authors": normalized_authors or fallback["authors"],
    }


def _local_name(tag):
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _child_text(parent, child_name):
    for child in parent:
        if _local_name(child.tag) == child_name and child.text and child.text.strip():
            return child.text.strip()
    return ""


def _parse_fb2_metadata_bytes(data):
    root = ET.fromstring(data)
    title = ""
    authors = []

    title_info = None
    for elem in root.iter():
        if _local_name(elem.tag) == "title-info":
            title_info = elem
            break

    if title_info is not None:
        title = _child_text(title_info, "book-title")

        for child in title_info:
            if _local_name(child.tag) != "author":
                continue
            first_name = _child_text(child, "first-name")
            middle_name = _child_text(child, "middle-name")
            last_name = _child_text(child, "last-name")
            nickname = _child_text(child, "nickname")
            parts = [part for part in [first_name, middle_name, last_name] if part]
            full_name = " ".join(parts) if parts else nickname
            if full_name:
                authors.append(full_name)

    return {
        "title": title or "Unknown Title",
        "authors": authors or ["Unknown Author"],
    }


def _parse_fb2_metadata_file(path):
    with open(path, "rb") as f:
        return _parse_fb2_metadata_bytes(f.read())


def _parse_epub_metadata_file(path):
    data = epub_meta.get_epub_metadata(path)
    return {
        "title": data.title or "Unknown Title",
        "authors": data.authors or ["Unknown Author"],
    }


def _parse_pdf_metadata_bytes(data):
    try:
        from pypdf import PdfReader
    except ModuleNotFoundError:
        return {
            "title": "",
            "authors": [],
        }

    reader = PdfReader(io.BytesIO(data))
    info = reader.metadata or {}
    title = (getattr(info, "title", "") or info.get("/Title", "") or "").strip()
    author = (getattr(info, "author", "") or info.get("/Author", "") or "").strip()
    authors = [author] if author else []
    return {
        "title": title,
        "authors": authors,
    }


def _parse_pdf_metadata_file(path):
    with open(path, "rb") as f:
        return _parse_pdf_metadata_bytes(f.read())


def _extract_zip_metadata(path):
    with zipfile.ZipFile(path) as zf:
        file_names = [name for name in zf.namelist() if not name.endswith("/")]
        candidates = []

        for ext in ZIP_METADATA_CANDIDATES:
            matches = [name for name in file_names if name.lower().endswith(ext)]
            candidates.extend(matches)

        if not candidates:
            raise ValueError("ZIP archive does not contain supported book files")

        entry_name = candidates[0]
        entry_ext = os.path.splitext(entry_name)[1].lower()

        if entry_ext == ".epub":
            with tempfile.NamedTemporaryFile(suffix=".epub") as temp_epub:
                temp_epub.write(zf.read(entry_name))
                temp_epub.flush()
                return _parse_epub_metadata_file(temp_epub.name), entry_name

        if entry_ext == ".fb2":
            return _parse_fb2_metadata_bytes(zf.read(entry_name)), entry_name

        if entry_ext == ".pdf":
            return _parse_pdf_metadata_bytes(zf.read(entry_name)), entry_name

        return _fallback_metadata(entry_name), entry_name


def _extract_book_metadata(path, extension):
    if extension == ".epub":
        return _parse_epub_metadata_file(path)
    if extension == ".fb2":
        return _parse_fb2_metadata_file(path)
    if extension == ".pdf":
        return _parse_pdf_metadata_file(path)
    if extension in {".mobi", ".azw", ".azw3", ".txt"}:
        return _fallback_metadata(path)
    if extension == ".zip":
        metadata, source_name = _extract_zip_metadata(path)
        return _normalize_metadata(metadata, source_name)

    raise ValueError(f"Unsupported extension: {extension}")


def _add_book_entry(feed, filename, metadata, mime_type):
    normalized_metadata = _normalize_metadata(metadata, filename)
    # Encode file names for safe HTTP links (spaces, Unicode, punctuation, etc.).
    encoded_filename = quote(filename, safe="")
    entry = SubElement(feed, "entry")
    SubElement(entry, "title").text = normalized_metadata["title"]
    author = SubElement(entry, "author")
    SubElement(author, "name").text = ", ".join(normalized_metadata["authors"])
    SubElement(entry, "id").text = f"urn:file:{filename}"
    SubElement(entry, "updated").text = datetime.utcnow().isoformat() + "Z"

    # Link to the actual file for download.
    SubElement(entry, "link", {
        "rel": "http://opds-spec.org/acquisition",
        "href": f"{BASE_URL}/books/{encoded_filename}",
        "type": mime_type,
    })

def create_opds():
    # Root Atom Feed
    feed = Element('feed', {
        'xmlns': 'http://www.w3.org/2005/Atom',
        'xmlns:dc': 'http://purl.org/dc/elements/1.1/',
        'xmlns:opds': 'http://opds-spec.org/2010/catalog'
    })
    
    SubElement(feed, 'id').text = "tag:github.com,2026:my-library"
    SubElement(feed, 'title').text = "My Personal Library"
    SubElement(feed, 'updated').text = datetime.utcnow().isoformat() + 'Z'
    SubElement(feed, 'link', {'rel': 'self', 'href': f"{BASE_URL}/{OUTPUT_FILE}", 'type': 'application/atom+xml;profile=opds-catalog;kind=navigation'})

    for filename in os.listdir(BOOKS_DIR):
        extension = os.path.splitext(filename)[1].lower()
        if extension not in MIME_TYPES:
            continue

        path = os.path.join(BOOKS_DIR, filename)
        try:
            metadata = _extract_book_metadata(path, extension)
            _add_book_entry(feed, filename, metadata, MIME_TYPES[extension])
        except Exception as e:
            print(f"Error processing {filename}: {e}")

    with open(OUTPUT_FILE, "wb") as f:
        f.write(tostring(feed, encoding='utf-8', xml_declaration=True))

if __name__ == "__main__":
    create_opds()