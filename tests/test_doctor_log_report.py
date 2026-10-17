"""init.py başlanğıcda `doctor.log_report` çağırır — funksiya silinəndə bot yalnız logda xəbərdarlıq verib davam edirdi."""
import logging
import re
import types

import common.doctor as doctor


def _check(status):
    return types.SimpleNamespace(status=status, icon="•", title="T", detail="D", fix_pacman="", fix_apt="")


def test_every_doctor_attribute_used_by_init_exists():
    src = open("init.py", encoding="utf-8").read()
    used = set(re.findall(r"\bdoctor\.(\w+)", src))
    assert used and not {n for n in used if not hasattr(doctor, n)}


def test_log_report_logs_each_level(caplog):
    log = logging.getLogger("t.doctor")
    with caplog.at_level(logging.INFO, logger="t.doctor"):
        doctor.log_report([_check("ok"), _check("warn")], log)
        doctor.log_report([_check("fail")], log)
        doctor.log_report([_check("ok")], log)
    msgs = [(r.levelname, r.getMessage()) for r in caplog.records]
    assert ("WARNING", "🩺 1 xəbərdarlıq — əsas funksiyalar işləyir.") in msgs
    assert any(l == "ERROR" and "1 ciddi problem" in m for l, m in msgs)
    assert ("INFO", "🩺 Bütün yoxlamalar qaydasındadır ✓") in msgs
