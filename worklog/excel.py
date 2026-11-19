"""ساخت فایل اکسل گزارش کارکرد ماهیانه در قالب شرکت."""

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.properties import PageSetupProperties

from .services import MONTHS_FA, report_data, user_display

DAYS = 31
TOTAL_COL = 2 + DAYS + 1  # A=ردیف، B=عنوان، C..AG=روزها، AH=جمع

THIN = Side(style='thin', color='9E9E9E')
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CENTER = Alignment(horizontal='center', vertical='center', wrap_text=True)
RIGHT = Alignment(horizontal='right', vertical='center', wrap_text=True)
LEFT = Alignment(horizontal='left', vertical='center', wrap_text=True)

FONT = Font(name='Tahoma', size=10)
FONT_BOLD = Font(name='Tahoma', size=10, bold=True)
FONT_TITLE = Font(name='Tahoma', size=16, bold=True)
FONT_WHITE = Font(name='Tahoma', size=12, bold=True, color='FFFFFF')

HEADER_FILL = PatternFill('solid', fgColor='D9E2F3')
TOTAL_FILL = PatternFill('solid', fgColor='EDEDED')
TITLE_FILL = PatternFill('solid', fgColor='1F4E78')


def _set(cell, value=None, font=FONT, fill=None, align=CENTER,
         border=True, number_format=None):
    if value is not None:
        cell.value = value
    cell.font = font
    if fill is not None:
        cell.fill = fill
    cell.alignment = align
    if border:
        cell.border = BORDER
    if number_format:
        cell.number_format = number_format
    return cell


def _paint_range(ws, row, col_from, col_to, font=FONT, fill=None,
                 align=CENTER, border=True, number_format=None):
    for col in range(col_from, col_to + 1):
        _set(
            ws.cell(row=row, column=col), font=font, fill=fill,
            align=align, border=border, number_format=number_format,
        )


def _merge(ws, row1, col1, row2, col2, value=None, font=FONT,
           fill=None, align=CENTER, border=True):
    ws.merge_cells(
        start_row=row1, start_column=col1,
        end_row=row2, end_column=col2,
    )
    # مرزها را روی همهٔ خانه‌های محدوده اعمال کن (اکسل برای merge مهم است).
    if border:
        for r in range(row1, row2 + 1):
            for c in range(col1, col2 + 1):
                ws.cell(row=r, column=c).border = BORDER
    cell = ws.cell(row=row1, column=col1)
    _set(cell, value=value, font=font, fill=fill, align=align, border=border)
    return cell


def build_worklog_xlsx(user, year, month):
    """خروجی: (بایت‌های xlsx، نام فایل)"""
    data = report_data(user, year, month)

    wb = Workbook()
    ws = wb.active
    ws.title = 'گزارش کارکرد'
    ws.sheet_view.rightToLeft = True

    # ---------- عرض ستون‌ها ----------
    ws.column_dimensions['A'].width = 6
    ws.column_dimensions['B'].width = 24
    for day in range(1, DAYS + 1):
        ws.column_dimensions[get_column_letter(2 + day)].width = 6.3
    ws.column_dimensions[get_column_letter(TOTAL_COL)].width = 11

    # ---------- سرصفحه ----------
    _merge(
        ws, 1, 1, 1, TOTAL_COL, 'بسمه تعالی',
        font=Font(name='Tahoma', size=12, bold=True),
    )
    ws.row_dimensions[1].height = 24

    _merge(
        ws, 2, 1, 2, TOTAL_COL, 'گزارش کارکرد ماهیانه',
        font=FONT_WHITE, fill=TITLE_FILL,
    )
    ws.row_dimensions[2].height = 32

    # ---------- مشخصات ----------
    info_row = 3
    _set(ws.cell(row=info_row, column=1), 'نام و نام خانوادگی:', align=RIGHT)
    _merge(ws, info_row, 2, info_row, 4, user_display(user), align=RIGHT)
    _set(ws.cell(row=info_row, column=5), 'ماه:', align=RIGHT)
    _merge(
        ws, info_row, 6, info_row, 7,
        MONTHS_FA[month - 1], align=CENTER,
    )
    _set(ws.cell(row=info_row, column=8), 'سال:', align=RIGHT)
    _merge(ws, info_row, 9, info_row, 9, str(year), align=CENTER)
    _set(ws.cell(row=info_row, column=10), 'تاریخ گزارش:', align=RIGHT)
    _merge(
        ws, info_row, 11, info_row, 14 + 3,
        data['report_date'], align=CENTER,
    )
    _paint_range(ws, info_row, 1, TOTAL_COL, align=RIGHT)
    ws.row_dimensions[info_row].height = 24

    # ---------- هدر دوطبقه ----------
    header1, header2 = 4, 5
    _merge(ws, header1, 1, header2, 1, 'ردیف', FONT_BOLD, HEADER_FILL)
    _merge(ws, header1, 2, header2, 2, 'عنوان فعالیت', FONT_BOLD, HEADER_FILL)
    _merge(ws, header1, 3, header1, 2 + DAYS, 'روز', FONT_BOLD, HEADER_FILL)
    _merge(ws, header1, TOTAL_COL, header2, TOTAL_COL,
           'جمع', FONT_BOLD, HEADER_FILL)
    for day in range(1, DAYS + 1):
        _set(
            ws.cell(row=header2, column=2 + day), day,
            font=FONT_BOLD, fill=HEADER_FILL,
        )
    _paint_range(ws, header2, 1, TOTAL_COL, font=FONT_BOLD,
                 fill=HEADER_FILL)
    ws.row_dimensions[header1].height = 22
    ws.row_dimensions[header2].height = 22

    # ---------- ردیف‌های فعالیت ----------
    start_row = 6
    min_rows = 16
    row_count = max(len(data['rows']), min_rows)
    row_index = 0
    for index in range(row_count):
        row_number = start_row + index
        source = data['rows'][index] if index < len(data['rows']) else None

        _set(ws.cell(row=row_number, column=1), index + 1)
        _set(
            ws.cell(row=row_number, column=2),
            source['title'] if source else '', align=RIGHT,
        )

        for day in range(1, DAYS + 1):
            cell = ws.cell(row=row_number, column=2 + day)
            value = source['values'][day - 1] if source else None
            if value and value > 0:
                _set(cell, float(value), number_format='0.00')
            else:
                _set(cell, None)

        total_value = source['total'] if source else None
        total_cell = ws.cell(row=row_number, column=TOTAL_COL)
        if total_value and total_value > 0:
            _set(total_cell, float(total_value), font=FONT_BOLD,
                 number_format='0.00')
        else:
            _set(total_cell, None, font=FONT_BOLD)

        # مرز کل ردیف
        for col in range(1, TOTAL_COL + 1):
            ws.cell(row=row_number, column=col).border = BORDER

    # ---------- ردیف جمع ----------
    total_row = start_row + row_count
    _merge(ws, total_row, 1, total_row, 2, 'جمع', FONT_BOLD, TOTAL_FILL)
    for day in range(1, DAYS + 1):
        value = data['total_by_day'][day - 1]
        _set(
            ws.cell(row=total_row, column=2 + day),
            float(value), font=FONT_BOLD, fill=TOTAL_FILL,
            number_format='0.00',
        )
    _set(
        ws.cell(row=total_row, column=TOTAL_COL),
        float(data['grand_total']), font=FONT_BOLD, fill=TOTAL_FILL,
        number_format='0.00',
    )
    _paint_range(ws, total_row, 1, TOTAL_COL, font=FONT_BOLD,
                 fill=TOTAL_FILL, number_format='0.00')
    ws.row_dimensions[total_row].height = 24

    # ---------- امضاها ----------
    sign_row = total_row + 2
    _merge(ws, sign_row, 1, sign_row, 11,
           'امضاء کارمند:', Font(name='Tahoma', size=10, bold=True),
           align=RIGHT)
    _merge(ws, sign_row, 12, sign_row, 22,
           'امضاء مسئول مستقیم:', Font(name='Tahoma', size=10, bold=True),
           align=CENTER)
    _merge(ws, sign_row, 23, sign_row, TOTAL_COL,
           'امضاء مدیر عامل:', Font(name='Tahoma', size=10, bold=True),
           align=CENTER)
    ws.row_dimensions[sign_row].height = 42

    # ---------- تنظیمات چاپ ----------
    ws.freeze_panes = 'C6'
    ws.print_title_rows = '1:5'
    ws.print_area = 'A1:%s%d' % (get_column_letter(TOTAL_COL), sign_row)
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.page_margins.left = 0.3
    ws.page_margins.right = 0.3
    ws.page_margins.top = 0.4
    ws.page_margins.bottom = 0.4

    buffer = BytesIO()
    wb.save(buffer)
    filename = 'worklog-%s-%s-%02d.xlsx' % (
        user.get_username(), year, month,
    )
    return buffer.getvalue(), filename
