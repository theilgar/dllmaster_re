#!/usr/bin/env python3
"""
🚚 Köçürmədən sonra bazadakı kompüterə bağlı ayarları düzəldir (deploy/restore_vds.sh çağırır).

Problem: bəzi ayarlar MÜTLƏQ yolla saxlanılır — məs. `yt:accounts` içində hər «server girişi» Chrome profilinin yolu
(`/run/media/user/Новый том/dllmaster_restructured/data/yt_profiles/<id>`). Yeni serverdə qovluq başqadır → hesablar «profil tapılmadı» olur.

  python3 deploy/fix_paths.py --old "/run/media/user/Новый том/dllmaster_restructured" --new /opt/dllmaster
  python3 deploy/fix_paths.py --old ... --new ... --vds      # + serverdə brauzer yoxdur: əsas hesab (ev brauzeri) rotasiyadan çıxır
  python3 deploy/fix_paths.py --old ... --new ... --dry-run  # heç nə yazmır, nəyi dəyişəcəyini göstərir

Təkrar işlədilə bilər (artıq düzəlmiş yollara toxunmur).
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def variants(path: str) -> list:
    """Yolun mətndə görünə biləcəyi formalar: açıq, JSON-escape (ensure_ascii: Kiril hərfləri \\uXXXX) və `\\/` ilə yazılmış."""
    path = path.rstrip("/")
    forms = {path, json.dumps(path)[1:-1], json.dumps(path, ensure_ascii=False)[1:-1]}
    forms |= {f.replace("/", "\\/") for f in forms}
    return sorted(forms, key=len, reverse=True)


def rewrite_text(text: str, old: str, new: str) -> tuple:
    """Mətndə köhnə kök yolunu yenisi ilə əvəz edir → (yeni mətn, əvəz sayı). Yalnız TAM yol komponenti (old/…) dəyişir."""
    new = new.rstrip("/")
    count = 0
    for form in variants(old):
        escaped = "\\/" in form
        sep, target = ("\\/", new.replace("/", "\\/")) if escaped else ("/", new)
        needle = form + sep
        if needle in text:
            count += text.count(needle)
            text = text.replace(needle, target + sep)
        if text == form:                                  # dəyər məhz kök qovluqdur
            text, count = target, count + 1
    return text, count


def vds_overrides(settings: dict) -> dict:
    """--vds: serverdə ev brauzeri yoxdur. ⭐ Əsas hesab (web:browser cookie-ləri) rotasiyadan çıxarılır, uzaq-server funksiyası
    söndürülür (VDS-in özündə SSH ilə başqa serverə yükləmə lazım deyil). Digər hesablar (kopyalanmış Chrome profilləri) qalır."""
    out = {"remote:on": "0"}
    try:
        main = json.loads(settings.get("yt:acc_main") or "{}")
    except ValueError:
        main = {}
    main["on"] = False
    out["yt:acc_main"] = json.dumps(main, ensure_ascii=False)
    return out


def plan(settings: dict, old: str, new: str, vds: bool = False) -> dict:
    """{açar: yeni dəyər} — yalnız dəyişəcək ayarlar."""
    changes = {}
    for key, value in settings.items():
        if isinstance(value, str) and any(form in value for form in variants(old)):
            fixed, n = rewrite_text(value, old, new)
            if n and fixed != value:
                changes[key] = fixed
    if vds:
        for key, value in vds_overrides({**settings, **changes}).items():
            if settings.get(key) != value:
                changes[key] = value
    return changes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--old", required=True, help="köhnə layihə qovluğu (MANIFEST.txt-də yazılır)")
    ap.add_argument("--new", default=ROOT, help="yeni layihə qovluğu (standart: bu repo)")
    ap.add_argument("--vds", action="store_true", help="server rejimi: əsas hesab söndürülür, uzaq-server funksiyası bağlanır")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    sys.path.insert(0, ROOT)
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, "config.env"))
    from common.database import get_db
    db = get_db()
    with db.sql() as cur:
        settings = dict(cur.execute("SELECT key, value FROM settings").fetchall())
    changes = plan(settings, args.old, args.new, args.vds)
    for key, value in sorted(changes.items()):
        print(f"{'[dry-run] ' if args.dry_run else ''}{key}: {str(settings.get(key))[:70]!r} → {value[:70]!r}")
    if not changes:
        print("Dəyişəcək ayar yoxdur (yollar artıq düzgündür).")
    elif not args.dry_run:
        for key, value in changes.items():
            db.set_setting(key, value)
        print(f"✅ {len(changes)} ayar yeniləndi.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
