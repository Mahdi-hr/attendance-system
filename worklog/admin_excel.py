"""خروجی اکسل جمعی پروژه‌ها برای مدیر (همهٔ کارمندان)."""

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.properties import PageSetupProperties

from .services import admin_project_summary, admin_project_year, user_display

THIN = Side(style='thin', color='9E9E9E')
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CENTER = Alignment(horizontal='center', vertical='center', wrap_text=True)
RIGHT = Alignment(horizontal='right', vertical='center', wrap_text=True)

FONT = Font(name='Tahoma', size=10)
FONT_BOLD = Font(name='Tahoma', size=10, bold=True)
FONT_WHITE = Font(name='Tahoma', size=13, bold=True, color='FFFFFF')

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


def _paint_row(ws, row, col_from, col_to, font=FONT, fill=None,
               align=CENTER, border=True, number_format=None):
    for col in range(col_from, col_to + 1):
        _set(
            ws.cell(row=row, column=col), font=font, fill=fill,
            align=align, border=border, number_format=number_format,
        )


def _merge(ws, row1, col1, row2, col2, value, font=FONT, fill=None,
           align=CENTER):
    ws.merge_cells(
        start_row=row1, start_column=col1,
        end_row=row2, end_column=col2,
    )
    for row in range(row1, row2 + 1):
        for col in range(col1, col2 + 1):
            ws.cell(row=row, column=col).border = BORDER
    cell = ws.cell(row=row1, column=col1)
    _set(cell, value=value, font=font, fill=fill, align=align)
    return cell


def _page_setup(ws, last_column, last_row):
    ws.sheet_view.rightToLeft = True
    ws.freeze_panes = 'C5'
    ws.print_title_rows = '1:4'
    ws.print_area = 'A1:%s%d' % (get_column_letter(last_column), last_row)
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.page_margins.left = 0.3
    ws.page_margins.right = 0.3
    ws.page_margins.top = 0.4
    ws.page_margins.bottom = 0.4


def _build_month_sheet(wb, year, month):
    data = admin_project_summary(year, month)
    ws = wb.active
    ws.title = 'جمع پروژه‌ها'

    users = data['users']
    last_col = 2 + len(users) + 1
    total_col = last_col

    ws.column_dimensions['A'].width = 6
    ws.column_dimensions['B'].width = 28
    for index in range(len(users)):
        ws.column_dimensions[get_column_letter(3 + index)].width = 13
    ws.column_dimensions[get_column_letter(total_col)].width = 12

    _merge(
        ws, 1, 1, 1, total_col,
        'گزارش جمع ساعت پروژه‌ها — همهٔ کارمندان',
        font=FONT_WHITE, fill=TITLE_FILL,
    )
    ws.row_dimensions[1].height = 30
    _merge(
        ws, 2, 1, 2, total_col,
        'ماه: %s  |  سال: %s  |  تعداد کارمندان: %s'
        % (data['month_label'], year, len(users)),
        font=FONT_BOLD, align=RIGHT,
    )
    ws.row_dimensions[2].height = 22

    header_row = 4
    _set(ws.cell(row=header_row, column=1), 'ردیف',
         font=FONT_BOLD, fill=HEADER_FILL)
    _set(ws.cell(row=header_row, column=2), 'پروژه',
         font=FONT_BOLD, fill=HEADER_FILL, align=RIGHT)
    for index, user in enumerate(users):
        _set(
            ws.cell(row=header_row, column=3 + index),
            user_display(user),
            font=FONT_BOLD, fill=HEADER_FILL,
        )
    _set(ws.cell(row=header_row, column=total_col), 'جمع کل',
         font=FONT_BOLD, fill=HEADER_FILL)
    _paint_row(ws, header_row, 1, total_col,
               font=FONT_BOLD, fill=HEADER_FILL)
    ws.row_dimensions[header_row].height = 30

    start_row = 5
    for index, project in enumerate(data['projects']):
        row = start_row + index
        _set(ws.cell(row=row, column=1), index + 1)
        _set(ws.cell(row=row, column=2), project['name'], align=RIGHT)
        for user_index, user in enumerate(users):
            value = project['by_user'].get(user.pk, 0)
            _set(
                ws.cell(row=row, column=3 + user_index),
                float(value) if value else None,
                number_format='0.00',
            )
        _set(
            ws.cell(row=row, column=total_col),
            float(project['total']) if project['total'] else None,
            font=FONT_BOLD, number_format='0.00',
        )
        _paint_row(ws, row, 1, total_col)
        _set(ws.cell(row=row, column=1), index + 1)
        _set(ws.cell(row=row, column=2), project['name'], align=RIGHT)
        _set(
            ws.cell(row=row, column=total_col),
            float(project['total']) if project['total'] else None,
            font=FONT_BOLD, number_format='0.00',
        )

    total_row = start_row + max(len(data['projects']), 1)
    _merge(ws, total_row, 1, total_row, 2, 'جمع', FONT_BOLD, TOTAL_FILL)
    for user_index, user in enumerate(users):
        value = data['total_by_user'].get(user.pk, 0)
        _set(
            ws.cell(row=total_row, column=3 + user_index),
            float(value) if value else None,
            font=FONT_BOLD, fill=TOTAL_FILL, number_format='0.00',
        )
    _set(
        ws.cell(row=total_row, column=total_col),
        float(data['grand_total']) if data['grand_total'] else None,
        font=FONT_BOLD, fill=TOTAL_FILL, number_format='0.00',
    )
    _paint_row(ws, total_row, 1, total_col,
               font=FONT_BOLD, fill=TOTAL_FILL, number_format='0.00')
    _set(ws.cell(row=total_row, column=1), 'جمع', font=FONT_BOLD,
         fill=TOTAL_FILL, align=RIGHT)
    ws.row_dimensions[total_row].height = 24

    _page_setup(ws, total_col, total_row)
    return data


def _build_year_sheet(wb, year):
    data = admin_project_year(year)
    ws = wb.create_sheet('روند سالانه')
    last_col = 2 + 12 + 1
    total_col = last_col

    ws.column_dimensions['A'].width = 6
    ws.column_dimensions['B'].width = 28
    for month in range(1, 13):
        ws.column_dimensions[get_column_letter(2 + month)].width = 8
    ws.column_dimensions[get_column_letter(total_col)].width = 12

    _merge(
        ws, 1, 1, 1, total_col,
        'روند ساعت پروژه‌ها در سال %s — همهٔ کارمندان' % year,
        font=FONT_WHITE, fill=TITLE_FILL,
    )
    ws.row_dimensions[1].height = 30
    _merge(
        ws, 2, 1, 2, total_col,
        'جمع ساعت ثبت‌شده روی هر پروژه، به تفکیک ماه',
        font=FONT_BOLD, align=RIGHT,
    )

    header_row = 4
    _set(ws.cell(row=header_row, column=1), 'ردیف',
         font=FONT_BOLD, fill=HEADER_FILL)
    _set(ws.cell(row=header_row, column=2), 'پروژه',
         font=FONT_BOLD, fill=HEADER_FILL, align=RIGHT)
    for index, month_name in enumerate(data['months']):
        _set(
            ws.cell(row=header_row, column=3 + index), month_name,
            font=FONT_BOLD, fill=HEADER_FILL,
        )
    _set(ws.cell(row=header_row, column=total_col), 'جمع سال',
         font=FONT_BOLD, fill=HEADER_FILL)
    _paint_row(ws, header_row, 1, total_col,
               font=FONT_BOLD, fill=HEADER_FILL)
    ws.row_dimensions[header_row].height = 30

    start_row = 5
    for index, project in enumerate(data['projects']):
        row = start_row + index
        _set(ws.cell(row=row, column=1), index + 1)
        _set(ws.cell(row=row, column=2), project['name'], align=RIGHT)
        for month_index, value in enumerate(project['values']):
            _set(
                ws.cell(row=row, column=3 + month_index),
                float(value) if value else None,
                number_format='0.00',
            )
        _set(
            ws.cell(row=row, column=total_col),
            float(project['total']) if project['total'] else None,
            font=FONT_BOLD, number_format='0.00',
        )
        _paint_row(ws, row, 1, total_col)
        _set(ws.cell(row=row, column=1), index + 1)
        _set(ws.cell(row=row, column=2), project['name'], align=RIGHT)
        _set(
            ws.cell(row=row, column=total_col),
            float(project['total']) if project['total'] else None,
            font=FONT_BOLD, number_format='0.00',
        )

    total_row = start_row + max(len(data['projects']), 1)
    _merge(ws, total_row, 1, total_row, 2, 'جمع', FONT_BOLD, TOTAL_FILL)
    for month_index, value in enumerate(data['total_by_month']):
        _set(
            ws.cell(row=total_row, column=3 + month_index),
            float(value) if value else None,
            font=FONT_BOLD, fill=TOTAL_FILL, number_format='0.00',
        )
    _set(
        ws.cell(row=total_row, column=total_col),
        float(data['grand_total']) if data['grand_total'] else None,
        font=FONT_BOLD, fill=TOTAL_FILL, number_format='0.00',
    )
    _paint_row(ws, total_row, 1, total_col,
               font=FONT_BOLD, fill=TOTAL_FILL, number_format='0.00')
    _set(ws.cell(row=total_row, column=1), 'جمع', font=FONT_BOLD,
         fill=TOTAL_FILL, align=RIGHT)
    ws.row_dimensions[total_row].height = 24

    _page_setup(ws, total_col, total_row)
    return data


def build_admin_projects_xlsx(year, month):
    """خروجی: (بایت‌های xlsx، نام فایل)"""
    wb = Workbook()
    _build_month_sheet(wb, year, month)
    _build_year_sheet(wb, year)
    buffer = BytesIO()
    wb.save(buffer)
    filename = 'worklog-projects-%s-%02d.xlsx' % (year, month)
    return buffer.getvalue(), filename
