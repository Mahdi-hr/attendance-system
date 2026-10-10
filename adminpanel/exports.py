"""خروجی اکسل/CSV برای عملیات گروهی پنل ادمین (بدون وابستگی سنگین)."""
import csv
from io import BytesIO

from django.http import HttpResponse

from recognition.excel_export import build_xlsx

XLSX_CONTENT_TYPE = (
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
)


def xlsx_response(filename, sheet_name, headers, rows, widths=None, rtl=True):
    book = build_xlsx(
        sheet_name=sheet_name,
        headers=headers,
        rows=rows,
        widths=widths,
        rtl=rtl,
    )
    response = HttpResponse(book, content_type=XLSX_CONTENT_TYPE)
    response['Content-Disposition'] = 'attachment; filename="%s"' % filename
    return response


def csv_response(filename, headers, rows):
    buffer = BytesIO()
    # BSSM اول فایل تا Excel فارسی را درست تشخیص دهد
    buffer.write('\ufeff'.encode('utf-8'))
    writer = csv.writer(buffer)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(['' if v is None else v for v in row])
    response = HttpResponse(buffer.getvalue(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="%s"' % filename
    return response
