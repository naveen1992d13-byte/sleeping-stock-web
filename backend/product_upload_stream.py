"""Bounded-memory Product Hub Excel parsing for POST /upload/v2.

Uses openpyxl read_only streaming and yields fixed-size insert batches so a
~10–20 MB inventory workbook stays well under the Testing MemoryMax envelope.
"""
from __future__ import annotations

import gc
import uuid
from io import BytesIO
from typing import Any, Callable, Dict, Iterator, List, Tuple

import openpyxl

# 1–2k rows per batch keeps peak RSS dominated by the read_only workbook window
# plus one batch of dicts, not the full sheet materialised in RAM.
UPLOAD_V2_BATCH_SIZE = 1500


def next_stream_batch(iterator):
    """Pull one (batch, stats) from a stream iterator; None when exhausted.

    Intended for asyncio.to_thread / run_in_executor so openpyxl CPU work stays
    off the event loop between awaits.
    """
    try:
        return next(iterator)
    except StopIteration:
        return None


def open_inventory_worksheet(raw_bytes: bytes):
    """Open workbook in read-only mode; prefer a sheet named Inventory."""
    wb = openpyxl.load_workbook(BytesIO(raw_bytes), read_only=True, data_only=True)
    ws = None
    for sheet_name in wb.sheetnames:
        if str(sheet_name).strip().lower() == "inventory":
            ws = wb[sheet_name]
            break
    if ws is None:
        ws = wb.active
    return wb, ws


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def stream_product_items(
    raw_bytes: bytes,
    *,
    upload_id: str,
    upload_no: str,
    now_iso: str,
    active_date_key: str,
    context: Dict[str, Any],
    format_excel_date: Callable[[Any], str],
    normalize_part_category: Callable[[str], str],
    stamp: Callable[[dict], dict],
    batch_size: int = UPLOAD_V2_BATCH_SIZE,
) -> Iterator[Tuple[List[dict], dict]]:
    """Yield (batch_docs, stats) for each flush.

    stats keys: rows_processed, rows_imported, rejected, total_available_qty, total_value
    """
    if batch_size < 1:
        batch_size = UPLOAD_V2_BATCH_SIZE

    wb, ws = open_inventory_worksheet(raw_bytes)
    batch: List[dict] = []
    pending = {
        "rows_processed": 0,
        "rows_imported": 0,
        "rejected": [],
        "total_available_qty": 0.0,
        "total_value": 0.0,
    }

    def _flush() -> Tuple[List[dict], dict]:
        nonlocal batch, pending
        out_batch = batch
        batch = []
        stats = pending
        pending = {
            "rows_processed": 0,
            "rows_imported": 0,
            "rejected": [],
            "total_available_qty": 0.0,
            "total_value": 0.0,
        }
        return out_batch, stats

    try:
        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if not row or not any(row):
                continue
            pending["rows_processed"] += 1
            part_no = str(row[0] or "").strip() if len(row) > 0 else ""
            if not part_no:
                pending["rejected"].append({"row": row_idx, "reason": "Part No Missing"})
                continue
            part_name = str(row[1] or "").strip() if len(row) > 1 else ""
            loc = str(row[2] or "").strip() if len(row) > 2 else ""
            qty = _safe_float(row[3] if len(row) > 3 else 0, 0.0)
            last_receipt = format_excel_date(row[4] if len(row) > 4 else "")
            last_sales = format_excel_date(row[5] if len(row) > 5 else "")
            mav = _safe_float(row[6] if len(row) > 6 else 0, 0.0)
            part_category_raw = str(row[7] or "").strip() if len(row) > 7 else ""
            part_category = normalize_part_category(part_category_raw)
            line_value = qty * mav
            pending["total_available_qty"] += qty
            pending["total_value"] += line_value
            pending["rows_imported"] += 1
            batch.append(
                stamp(
                    {
                        "id": str(uuid.uuid4()),
                        "upload_id": upload_id,
                        "upload_no": upload_no,
                        "upload_type": "product",
                        "part_number": part_no,
                        "item_name": part_name,
                        "loc": loc,
                        "bin_location": loc,
                        "quantity": qty,
                        "available_qty": qty,
                        "last_receipt_date": last_receipt,
                        "last_sales_date": last_sales,
                        "mav": mav,
                        "mav_value": mav,
                        "total_value": line_value,
                        "part_category": part_category,
                        "available_qty_number": float(qty),
                        "unit_value_number": float(mav),
                        "total_value_number": float(line_value),
                        "publish_status": "Waiting",
                        "active_date_key": active_date_key,
                        "created_at": now_iso,
                        **context,
                    }
                )
            )
            if len(batch) >= batch_size:
                yield _flush()
                gc.collect()
        if batch or pending["rows_processed"] or pending["rejected"]:
            yield _flush()
    finally:
        try:
            wb.close()
        except Exception:
            pass
        gc.collect()


def build_synthetic_inventory_xlsx(
    min_bytes: int = 20 * 1024 * 1024,
    *,
    start_rows: int = 25000,
) -> Tuple[bytes, int]:
    """Build an Inventory-sheet workbook at least min_bytes; returns (bytes, data_row_count).

    Uses direct OOXML zip writing (not openpyxl write_only) so a 20 MB+ harness
    file can be produced quickly on the small Testing host.
    """
    import zipfile

    # ~800–1200 bytes/row of mostly incompressible padding → reach 20 MB in one pass.
    rows = max(1000, int(start_rows))
    pad = "X" * 700

    def _sheet_xml(n: int) -> bytes:
        out = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
            "<sheetData>",
            '<row r="1">'
            '<c r="A1" t="inlineStr"><is><t>Part Number</t></is></c>'
            '<c r="B1" t="inlineStr"><is><t>Part Name</t></is></c>'
            '<c r="C1" t="inlineStr"><is><t>Bin</t></is></c>'
            '<c r="D1" t="inlineStr"><is><t>Qty</t></is></c>'
            '<c r="E1" t="inlineStr"><is><t>Last Receipt</t></is></c>'
            '<c r="F1" t="inlineStr"><is><t>Last Sales</t></is></c>'
            '<c r="G1" t="inlineStr"><is><t>MAV</t></is></c>'
            '<c r="H1" t="inlineStr"><is><t>Category</t></is></c>'
            "</row>",
        ]
        for i in range(n):
            r = i + 2
            part = f"PART-{i:06d}-SYNTH"
            name = f"Name-{i}-{pad}"
            out.append(
                f'<row r="{r}">'
                f'<c r="A{r}" t="inlineStr"><is><t>{part}</t></is></c>'
                f'<c r="B{r}" t="inlineStr"><is><t>{name}</t></is></c>'
                f'<c r="C{r}" t="inlineStr"><is><t>BIN{(i % 500):03d}</t></is></c>'
                f'<c r="D{r}"><v>{(i % 50) + 1}</v></c>'
                f'<c r="E{r}" t="inlineStr"><is><t>2026-01-15</t></is></c>'
                f'<c r="F{r}" t="inlineStr"><is><t>2026-02-20</t></is></c>'
                f'<c r="G{r}"><v>{(i % 100) + 0.5}</v></c>'
                f'<c r="H{r}" t="inlineStr"><is><t>Fast Moving</t></is></c>'
                f"</row>"
            )
        out.append("</sheetData></worksheet>")
        return "".join(out).encode("utf-8")

    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>
"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>
"""
    wb_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="Inventory" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>
"""
    wb_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>
"""

    while True:
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr("[Content_Types].xml", content_types)
            zf.writestr("_rels/.rels", rels)
            zf.writestr("xl/workbook.xml", wb_xml)
            zf.writestr("xl/_rels/workbook.xml.rels", wb_rels)
            zf.writestr("xl/worksheets/sheet1.xml", _sheet_xml(rows))
        data = buf.getvalue()
        if len(data) >= min_bytes:
            return data, rows
        rows = int(rows * 1.25) + 2000
