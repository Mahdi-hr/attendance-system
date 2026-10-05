"""Minimal, dependency-free XLSX writer (Russian doll of XML + zipfile).

Enough of the OOXML spreadsheet spec to produce a clean, styled .xlsx that
opens in MS Excel, LibreOffice, WPS and Google Sheets. No third-party deps.
"""
import re
import zipfile
from io import BytesIO

_XML_ESCAPE = re.compile(r'[<>&"\x00-\x08\x0b\x0c\x0e-\x1f]')


def _esc(value):
    return _XML_ESCAPE.sub(lambda m: {
        '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;',
    }.get(m.group(0), ''), str(value))


def _col_letter(index):
    letters = ''
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _cell(row, col, value, style):
    ref = '%s%d' % (_col_letter(col), row)
    if isinstance(value, bool):
        return '<c r="%s" s="%s"><v>%d</v></c>' % (ref, style, int(value))
    if isinstance(value, (int, float)):
        return '<c r="%s" s="%s" t="n"><v>%s</v></c>' % (ref, style, str(value))
    text = '' if value is None else str(value)
    # Guard against spreadsheet formula injection from user-attendance data.
    if text[:1] in '=+-@':
        text = "'" + text
    return '<c r="%s" s="%s" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (ref, style, _esc(text))


_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

_WORKBOOK = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="{name}" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""

_WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="3">
    <font><sz val="11"/><name val="Calibri"/></font>
    <font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>
    <font><b/><sz val="11"/><name val="Calibri"/></font>
  </fonts>
  <fills count="4">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FF4F46E5"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFEEF2FF"/><bgColor indexed="64"/></patternFill></fill>
  </fills>
  <borders count="2">
    <border><left/><right/><top/><bottom/><diagonal/></border>
    <border>
      <left style="thin"><color rgb="FFCBD5E1"/></left>
      <right style="thin"><color rgb="FFCBD5E1"/></right>
      <top style="thin"><color rgb="FFCBD5E1"/></top>
      <bottom style="thin"><color rgb="FFCBD5E1"/></bottom>
      <diagonal/>
    </border>
  </borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="4">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"><alignment horizontal="center" vertical="center"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1"><alignment vertical="center"/></xf>
    <xf numFmtId="0" fontId="0" fillId="3" borderId="1" xfId="0" applyFill="1" applyBorder="1"><alignment vertical="center"/></xf>
  </cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


def _worksheet(headers, rows, widths, rtl):
    body = []

    total_rows = len(rows) + 1
    total_cols = max(len(headers), max((len(r) for r in rows), default=0))

    # Column widths
    if widths:
        cols = ''.join(
            '<col min="%d" max="%d" width="%s" customWidth="1"/>' % (i + 1, i + 1, w)
            for i, w in enumerate(widths)
        )
    else:
        cols = ''
    body.append('<cols>%s</cols>' % cols if cols else '')

    # Header row
    hcells = ''.join(_cell(1, c, h, 1) for c, h in enumerate(headers))
    body.append('<row r="1" ht="22" customHeight="1">%s</row>' % hcells)

    # Data rows (zebra: even → plain, odd → light fill)
    for r, row in enumerate(rows, start=2):
        cells = []
        for c in range(total_cols):
            value = row[c] if c < len(row) else None
            style = 3 if (r % 2 == 1) else 2
            cells.append(_cell(r, c, value, style))
        body.append('<row r="%d">%s</row>' % (r, ''.join(cells)))

    rtl_attr = ' rightToLeft="1"' if rtl else ''
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetViews><sheetView workbookViewId="0"%s/></sheetViews>%s'
            '<sheetData>%s</sheetData></worksheet>') % (rtl_attr, cols, ''.join(body))


def build_xlsx(sheet_name, headers, rows, widths=None, rtl=True):
    """Return bytes of a valid .xlsx file."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('[Content_Types].xml', _CONTENT_TYPES)
        zf.writestr('_rels/.rels', _ROOT_RELS)
        zf.writestr('xl/workbook.xml', _WORKBOOK.format(name=_esc(sheet_name)[:31]))
        zf.writestr('xl/_rels/workbook.xml.rels', _WORKBOOK_RELS)
        zf.writestr('xl/styles.xml', _STYLES)
        zf.writestr('xl/worksheets/sheet1.xml', _worksheet(headers, rows, widths, rtl))
    return buffer.getvalue()