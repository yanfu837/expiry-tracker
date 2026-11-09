"""Local OCR and conservative field extraction. Documents are never executed."""
import ctypes
import ctypes.util
import csv
import errno
import io
import os
import re
import resource
import subprocess
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

MAX_TEXT = 200_000
MAX_PAGES = 10


class ProcessingFailure(Exception):
    def __init__(self, code="unavailable"):
        self.code = code
        super().__init__(code)


def sandbox_child(memory_mb, seconds):
    """Bound parsers and block all new sockets, including outbound networking."""
    resource.setrlimit(resource.RLIMIT_AS, (memory_mb*1024*1024, memory_mb*1024*1024))
    resource.setrlimit(resource.RLIMIT_CPU, (max(1, seconds), max(2, seconds+1)))
    resource.setrlimit(resource.RLIMIT_FSIZE, (32*1024*1024, 32*1024*1024))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    library = ctypes.util.find_library("seccomp")
    if not library:
        raise RuntimeError("Local parser sandbox unavailable")
    seccomp = ctypes.CDLL(library)
    seccomp.seccomp_init.restype = ctypes.c_void_p
    seccomp.seccomp_init.argtypes = [ctypes.c_uint32]
    seccomp.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    seccomp.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    seccomp.seccomp_load.argtypes = [ctypes.c_void_p]
    seccomp.seccomp_release.argtypes = [ctypes.c_void_p]
    context = seccomp.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
    if not context:
        raise RuntimeError("Local parser sandbox unavailable")
    try:
        for name in (b"socket", b"socketpair", b"connect", b"sendto", b"sendmsg", b"sendmmsg"):
            syscall = seccomp.seccomp_syscall_resolve_name(name)
            if syscall >= 0 and seccomp.seccomp_rule_add(context, 0x00050000 | errno.EPERM, syscall, 0) != 0:
                raise RuntimeError("Local parser sandbox unavailable")
        if seccomp.seccomp_load(context) != 0:
            raise RuntimeError("Local parser sandbox unavailable")
    finally:
        seccomp.seccomp_release(context)


def run_tool(arguments, deadline, memory_mb=1024, output=None):
    remaining = min(45, int(deadline-time.monotonic()))
    if remaining <= 0:
        raise ProcessingFailure("timeout")
    # Do not pass the service's authentication token or proxy credentials to parsers.
    environment = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8",
                   "HOME": str(Path(arguments[-1]).parent) if output else "/tmp", "OMP_THREAD_LIMIT": "1"}
    try:
        return subprocess.run(arguments, stdin=subprocess.DEVNULL, stdout=output or subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=remaining, env=environment,
                              preexec_fn=lambda: sandbox_child(memory_mb, remaining), check=False)
    except (OSError, subprocess.SubprocessError):
        raise ProcessingFailure("unavailable") from None


def _decode(raw):
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig")


def validate_signature(path):
    with path.open("rb") as source:
        header = source.read(1024)
    suffix = path.suffix
    valid = True
    if suffix == ".pdf":
        valid = header.startswith(b"%PDF-")
    elif suffix == ".png":
        valid = header.startswith(b"\x89PNG\r\n\x1a\n")
    elif suffix in {".jpg", ".jpeg"}:
        valid = header.startswith(b"\xff\xd8\xff")
    elif suffix == ".webp":
        valid = header.startswith(b"RIFF") and header[8:12] == b"WEBP"
    elif suffix in {".doc", ".xls"}:
        valid = header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    elif suffix in {".docx", ".xlsx"}:
        try:
            with zipfile.ZipFile(path) as archive:
                entries = archive.infolist()
                required = "word/document.xml" if suffix == ".docx" else "xl/workbook.xml"
                valid = (required in archive.namelist() and len(entries) <= 1000
                         and sum(e.file_size for e in entries) <= 64*1024*1024
                         and not any(e.flag_bits & 1 or e.filename.lower().endswith("vbaproject.bin") for e in entries))
        except zipfile.BadZipFile:
            valid = False
    elif suffix in {".txt", ".csv"}:
        try:
            valid = "\x00" not in _decode(path.read_bytes())
        except UnicodeError:
            valid = False
    if not valid:
        raise ProcessingFailure("invalid_file")


def _office_text(path):
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 1000 or sum(e.file_size for e in entries) > 64*1024*1024:
                raise ProcessingFailure("unsupported")
            if any(e.flag_bits & 1 or e.file_size > 8*1024*1024 for e in entries):
                raise ProcessingFailure("unsupported")
            if any(e.filename.lower().endswith("vbaproject.bin") for e in entries):
                raise ProcessingFailure("unsupported")
            names = (["word/document.xml"] if path.suffix == ".docx" else
                     [e.filename for e in entries if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", e.filename)])
            shared = []
            date_styles = set()
            epoch = date(1899, 12, 30)
            if path.suffix == ".xlsx":
                if "xl/sharedStrings.xml" in archive.namelist():
                    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
                    shared = ["".join(node.itertext()) for node in root]
                if "xl/workbook.xml" in archive.namelist():
                    root = ElementTree.fromstring(archive.read("xl/workbook.xml"))
                    if any(node.attrib.get("date1904") in {"1", "true"} for node in root.iter()):
                        epoch = date(1904, 1, 1)
                if "xl/styles.xml" in archive.namelist():
                    root = ElementTree.fromstring(archive.read("xl/styles.xml"))
                    custom_dates = {int(node.attrib["numFmtId"]) for node in root.iter()
                                    if node.tag.rsplit("}", 1)[-1] == "numFmt"
                                    and re.search(r"[yd]", node.attrib.get("formatCode", ""), re.I)}
                    for node in root:
                        if node.tag.rsplit("}", 1)[-1] == "cellXfs":
                            date_styles = {index for index, style in enumerate(node)
                                           if int(style.attrib.get("numFmtId", "0")) in set(range(14, 23)) | custom_dates}
            texts = []
            for name in names:
                root = ElementTree.fromstring(archive.read(name))
                if path.suffix == ".docx":
                    for paragraph in root.iter():
                        if paragraph.tag.rsplit("}", 1)[-1] == "p":
                            texts.append("".join(node.text or "" for node in paragraph.iter()
                                                 if node.tag.rsplit("}", 1)[-1] == "t"))
                else:
                    for row in root.iter():
                        if row.tag.rsplit("}", 1)[-1] == "row":
                            cells = []
                            for cell in row:
                                value = "".join(node.text or "" for node in cell.iter()
                                                if node.tag.rsplit("}", 1)[-1] in {"v", "t"})
                                if cell.attrib.get("t") == "s":
                                    value = shared[int(value)]
                                elif int(cell.attrib.get("s", "0")) in date_styles and value:
                                    number = float(value)
                                    if 0 <= number <= 110_000:
                                        adjustment = 1 if epoch.year == 1899 and 0 < number < 60 else 0
                                        value = (epoch+timedelta(days=int(number)+adjustment)).isoformat()
                                cells.append(value)
                            texts.append("\t".join(cells))
                if sum(map(len, texts)) >= MAX_TEXT:
                    break
            return "\n".join(texts)[:MAX_TEXT]
    except (zipfile.BadZipFile, KeyError, ValueError, IndexError, OverflowError, ElementTree.ParseError, DefusedXmlException):
        raise ProcessingFailure("unsupported") from None


def read_document(path, deadline):
    warnings = []
    suffix = path.suffix
    if suffix in {".txt", ".csv"}:
        try:
            text = _decode(path.read_bytes())
        except UnicodeError:
            raise ProcessingFailure("unsupported") from None
        if "\x00" in text:
            raise ProcessingFailure("unsupported")
    elif suffix in {".docx", ".xlsx"}:
        text = _office_text(path)
    elif suffix in {".doc", ".xls"}:
        command = ["antiword", str(path)] if suffix == ".doc" else ["xls2csv", str(path)]
        result = run_tool(command, deadline)
        if result.returncode:
            raise ProcessingFailure("unsupported")
        text = result.stdout.decode("utf-8", errors="replace")
    elif suffix == ".pdf":
        info = run_tool(["/usr/bin/pdfinfo", str(path)], deadline)
        pages = re.search(rb"^Pages:\s+(\d+)", info.stdout, re.MULTILINE)
        if info.returncode or not pages or re.search(rb"Encrypted:\s+yes", info.stdout):
            raise ProcessingFailure("unsupported")
        count = int(pages.group(1))
        if count > MAX_PAGES:
            warnings.append(f"只辨識前 {MAX_PAGES} 頁，請確認後續頁面是否還有到期資訊。")
        texts = []
        for number in range(1, min(count, MAX_PAGES)+1):
            result = run_tool(["pdftotext", "-f", str(number), "-l", str(number), "-layout", str(path), "-"], deadline)
            page_text = result.stdout.decode("utf-8", errors="replace")
            if len(re.sub(r"\s", "", page_text)) < 20:
                prefix = path.parent / "ocr-page"
                image = prefix.with_suffix(".png")
                converted = run_tool(["/usr/bin/pdftoppm", "-f", str(number), "-l", str(number), "-singlefile",
                                      "-scale-to", "2000", "-png", str(path), str(prefix)], deadline)
                if converted.returncode or not image.exists():
                    raise ProcessingFailure("unsupported")
                try:
                    page_text = ocr_image(image, deadline)
                finally:
                    image.unlink(missing_ok=True)
            texts.append(page_text)
        text = "\n".join(texts)
    else:
        text = ocr_image(path, deadline)
    if len(text) >= MAX_TEXT:
        warnings.append("文件文字過多，只辨識部分內容，請自行核對。")
    return text[:MAX_TEXT], warnings


def ocr_image(path, deadline):
    result = run_tool(["tesseract", str(path), "stdout", "-l", os.environ.get("OCR_LANGUAGES", "eng+chi_tra"), "--psm", "6"], deadline)
    if result.returncode:
        raise ProcessingFailure("unsupported")
    return result.stdout.decode("utf-8", errors="replace")[:MAX_TEXT]


_DATE = re.compile(r"(?:(民國)\s*)?(\d{3,4})\s*(?:年|[-/.])\s*(\d{1,2})\s*(?:月|[-/.])\s*(\d{1,2})\s*日?")
_EXPIRY = re.compile(r"到期|有效(?:期限|至|迄)|截止|屆滿|expires?|expiry|expiration|valid\s+(?:until|through)|end\s+date", re.I)
_PERIOD = re.compile(r"有效期間|保險期間|coverage\s+period|validity\s+period", re.I)


def table_suggestions(text, warnings):
    delimiter = "\t" if "\t" in text else ","
    try:
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    except csv.Error:
        return []
    for number, header in enumerate(rows[:10]):
        columns = {}
        for index, value in enumerate(header):
            label = value.strip()
            if _EXPIRY.search(label) and not _DATE.search(label):
                columns["Expiry date"] = index
            elif re.fullmatch(r"名稱|文件名稱|合約名稱|保單名稱|證照名稱|name|title|document[ _]+(?:name|title)|policy[ _]+name", label, re.I):
                columns["Document title"] = index
            elif re.fullmatch(r"備註|注意事項|notes?|remarks?", label, re.I):
                columns["Notes"] = index
        if "Expiry date" not in columns or len(columns) < 2:
            continue
        records = [row for row in rows[number+1:] if any(cell.strip() for cell in row)]
        if len(records) > 1:
            warnings.append("表格包含多筆資料，請確認要建立的項目；不同到期日不會自動合併。")
        if len(records) > 20:
            warnings.append("僅檢查表格前 20 筆資料，請自行核對後續內容。")
        return [f"{label}: {row[index]}" for row in records[:20]
                for label, index in columns.items() if index < len(row) and row[index].strip()]
    return []


def extract_fields(text, warnings=None):
    warnings = list(warnings or [])
    text = re.sub(r"(?<=[\u3400-\u9fff]) +(?=[\u3400-\u9fff])", "", text)
    suggestions = table_suggestions(text, warnings)
    if suggestions:
        text += "\n" + "\n".join(suggestions)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    dates = {}
    for index, line in enumerate(lines):
        if not (_EXPIRY.search(line) or _PERIOD.search(line)):
            continue
        source = line
        if not _DATE.search(line):
            if index+1 >= len(lines) or not _DATE.match(lines[index+1]):
                continue
            remainder = _DATE.sub("", lines[index+1])
            if remainder.strip() and not re.fullmatch(r"(?:\s|至|迄|~|～|–|—|to|until|through)+", remainder, re.I):
                continue
            source += " " + lines[index+1]
        matches = list(_DATE.finditer(source))
        if _PERIOD.search(line) and re.search(r"至|迄|until|through|\bto\b|[~～–—]", source, re.I):
            matches = matches[-1:]
        for match in matches:
            try:
                year = int(match[2])
                if year < 1000:
                    year += 1911
                parsed = date(year, int(match[3]), int(match[4]))
                if 1900 <= parsed.year <= 2200:
                    dates[parsed.isoformat()] = source[:300]
            except ValueError:
                continue
    expiry = next(iter(dates)) if len(dates) == 1 else None
    if not dates:
        warnings.append("未找到明確標示的到期日，請自行填寫；系統不會以其他日期代替。")
    elif len(dates) > 1:
        warnings.append("文件有多個不同的到期日，請自行選擇。")
    name = None
    for line in lines:
        match = re.match(r"(?:文件名稱|合約名稱|保單名稱|證照名稱|名稱|document\s+(?:title|name)|policy\s+name)\s*[:：]\s*(.+)", line, re.I)
        if match:
            name = match[1][:200]
            break
    if not name:
        name = next((line[:200] for line in lines[:3] if 3 <= len(line) <= 100
                     and not (_DATE.search(line) or _EXPIRY.search(line) or _PERIOD.search(line))
                     and not re.match(r"(?:備註|注意事項|notes?|remarks?)\s*[:：]", line, re.I)), None)
    evidence = dates.get(expiry, "")
    notes = ""
    for line in lines:
        match = re.match(r"(?:備註|注意事項|notes?|remarks?)\s*[:：]\s*(.+)", line, re.I)
        if match:
            notes = match[1][:1000]
            break
    return {"name": name, "expiry_date": expiry, "notes": notes, "evidence": evidence,
            "warnings": warnings[:5], "method": "本機 OCR 與欄位規則擷取"}
