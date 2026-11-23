# -*- coding: utf-8 -*-
"""Notifications in the shape the code before step 5 (and 1.8.x) stores them - spelled out, Czech keys and values.

Used by tests/test_conversion_values.py; its expectations were measured on that code over this very state.
The SMTP password is a made-up one: the test checks it shows nowhere.
"""
from __future__ import annotations

from typing import Any

PASSWORD = "SMTP-heslo-jen-pro-test-91"
OLD = {
    "notify_smtp_enabled": "1", "notify_smtp_host": "smtp.test", "notify_smtp_port": "587",
    "notify_smtp_uzivatel": "jana", "notify_smtp_heslo": PASSWORD, "notify_smtp_odesilatel": "jellyscope@test",
    "notify_smtp_komu": "admin@test", "notify_smtp_tls": "1",
    "notify_discord_enabled": "1", "notify_discord_webhook": "https://discord.test/hook/abc",
    "notify_event_sberac": "1", "notify_event_misto": "0", "notify_event_souhrn": "1",
    "notify_state_sberac": "spatne", "notify_state_misto": "ok",
    "notify_text_sberac_porucha_predmet": "Sběrač stojí {minutes} min",
    "notify_text_sberac_porucha_text": "Vlastní text: {error}",
    "notify_text_souhrn_predmet": "Týden v Jellyscope",
    "notify_ticho_minut": "45", "notify_misto_dnu": "10", "notify_souhrn_den": "2", "notify_souhrn_cas": "08:30",
    "notify_souhrn_odeslan": "2026-11-16", "notify_sberac_chyba_od": "2026-11-21 10:00:00",
    "task_notifikace_enabled": "1", "task_notifikace_last_auto": "2026-11-21 23:00:00",
}
# The new names of the keys above (a database of 1.8.x has none of them; the test deletes them first).
NEW_PREFIXES = ("notify_smtp_user", "notify_smtp_password", "notify_smtp_sender", "notify_smtp_recipient", "notify_event_",
                "notify_state_", "notify_text_", "notify_quiet_minutes", "notify_space_days", "notify_summary_",
                "notify_collector_error_since", "task_notifications_")


def write_old(db: Any) -> None:
    with db.connect() as conn:
        for prefix in NEW_PREFIXES:
            conn.execute("DELETE FROM settings WHERE key LIKE ?", (prefix + "%",))
        for key, value in OLD.items():
            conn.execute("DELETE FROM settings WHERE key = ?", (key,))
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.execute("INSERT INTO scan_log (kind, started_at, finished_at, status, message)"
                     " VALUES ('notifikace', '2026-11-21 23:00:00', '2026-11-21 23:00:01', 'done', 'Nothing new to report.')")
        conn.commit()
