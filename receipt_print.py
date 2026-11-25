"""Печать чека заказа: предпросмотр и отправка на принтер (первая и повторная).

Работает только с уже загруженным заказом и локальным журналом печати. Объекта базы данных здесь нет: повторная печать не создаёт заказ,
не проводит оплату и не списывает товары. Номер копии берётся из журнала: 0 — первая печать, дальше — повторные (на чеке помечены).
"""

from dataclasses import dataclass
from datetime import datetime

import receipt as rc
import receipt_render as rr
from print_log import PrintLog
from printing import PrintError, PrinterConfig, send

TIME_FMT = "%d.%m.%Y %H:%M"


@dataclass
class Prepared:
    receipt: rc.Receipt
    image: object             # PIL.Image или None, если Pillow не установлен
    lost: str                 # символы, которых нет в шрифте (заменены на «?»)
    copy_no: int


@dataclass
class PrintResult:
    copy_no: int
    lost: str
    logged: bool              # False — чек напечатан, но журнал печати записать не удалось


def prepare(order, cfg=None, log=None, now=None, with_image=True):
    """Чек для предпросмотра и печати. Ошибка заказа — receipt.ReceiptError."""
    cfg, log = cfg or PrinterConfig(), log or PrintLog(cfg.log_path or None if cfg else None)
    copy_no = log.count_ok(order.get("id", 0)) if isinstance(order, dict) and order.get("id") else 0
    receipt = rc.build_receipt(order, cafe_name=cfg.cafe_name or rc.DEFAULT_CAFE, copy_no=copy_no,
                               printed_at=(now or datetime.now()).strftime(TIME_FMT), payment_method=order.get("payment_method", "") or "", cols=rc.cols_for_width(cfg.width_dots))
    image, lost = (None, "")
    if with_image and rr.available():
        image, lost = rr.render_receipt(receipt, width_dots=cfg.width_dots)
    return Prepared(receipt, image, lost, copy_no)


def print_order(order, cfg, log=None, now=None):
    """Печатает чек. Возвращает PrintResult или бросает PrintError (принтер не настроен / не найден / не отвечает и т. п.)."""
    if order.get("voided"):
        raise PrintError("Заказ аннулирован: чек не печатается.", "voided")
    if not rr.available():
        raise PrintError("Для печати нужна библиотека Pillow (pip install Pillow).", "no_pillow")
    log = log or PrintLog(cfg.log_path or None)
    try:
        prepared = prepare(order, cfg, log, now)
        data = rr.escpos_job(prepared.image, cut=cfg.cut)
    except rr.RenderError as e:
        raise PrintError(str(e), "failed") from e
    kind = "reprint" if prepared.copy_no else "print"
    try:
        send(cfg, data)
    except PrintError as e:
        log.record(order["id"], False, kind, cfg.describe(), str(e), now)       # неудача записывается, но копией не считается
        raise
    logged = log.record(order["id"], True, kind, cfg.describe(), "", now)
    return PrintResult(prepared.copy_no, prepared.lost, logged)
