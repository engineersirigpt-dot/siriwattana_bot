"""READ-ONLY check of a database account before the bot's data lookups use it.

Run on the prod server, from the repo (nothing is installed or changed):

    nano ~/newdb.py          # 4 lines, never committed or shared:
                             #   import os
                             #   os.environ["FACTORY_DB_SERVER"] = "192.168.5.19"
                             #   os.environ["FACTORY_DB_USER"] = "..."
                             #   os.environ["FACTORY_DB_PASSWORD"] = "..."
    chmod 600 ~/newdb.py
    cat ~/newdb.py backend/check_factory_db.py | docker exec -i siriwattana-backend-prod python -

It prints account names, yes/no permission flags (sysadmin, HR databases),
the tables the lookups need that the account cannot read, and the bot's own
lookups run through the account with timings. No passwords, names or rows.
If it passes, put the same FACTORY_DB_* values in backend/.env.prod and
recreate the backend; removing them goes back to the MI account.
"""
import os as _os
import sys as _sys
import time as _time

import pymssql as _pymssql

_sys.stdout.reconfigure(encoding="utf-8")

FLAGS = ("SELECT SUSER_SNAME() AS login_name, IS_SRVROLEMEMBER('sysadmin') AS sysadmin, "
         "HAS_PERMS_BY_NAME(NULL, NULL, 'CONTROL SERVER') AS control_server, "
         "HAS_PERMS_BY_NAME(NULL, NULL, 'ALTER ANY LOGIN') AS alter_any_login")
NEED = ["mi.dbo.mi", "mi.dbo.customer", "mi.dbo.mi_close_job", "mi.dbo.machine_planning", "mi.dbo.machine",
        "mi.dbo.timesheet_header", "mi.dbo.timesheet_item", "mi.dbo.Si_jobopen_man", "mi.dbo.Si_jobopen_packing",
        "mi.dbo.mi_delivery", "mi.dbo.mi_delivery_docket", "mi.dbo.mi_delivery_docket_detail",
        "mi.dbo.FG_Product_Pallet", "mi.dbo.tb_timesheet_checklist_qc_head",
        "ESTIMATE.dbo.tb_rfq_list", "ESTIMATE.dbo.tb_rfq_price1", "ESTIMATE.dbo.tb_rfq_status_log",
        "MI2_ESTIMATE.dbo.tb_rfq", "MI2_ESTIMATE.dbo.tb_quotation",
        "QC_MASTER.dbo.tb_qc_head", "QC_MASTER.dbo.tb_qc_detail", "QC_MASTER.dbo.tb_qc_problem",
        "MAINTENANCE_MACHINE.dbo.ma_request", "MAINTENANCE_MACHINE.dbo.v_machine"]
SHOULD_NOT = ["HRM", "OT_HRM", "LEAVE_FLOW", "EVALUATE_EMPLOYEE", "HR", "MI2_AUTHEN"]


def _rows(conn, sql):
    cur = conn.cursor(as_dict=True)
    cur.execute(sql)
    return cur.fetchall()


# 1) the account in use today (MI_DB_*, via .10 and its linked server)
try:
    old = _pymssql.connect(server=_os.environ["MI_DB_SERVER"], port=int(_os.environ.get("MI_DB_PORT", "1433")),
                           user=_os.environ["MI_DB_USER"], password=_os.environ["MI_DB_PASSWORD"],
                           database="master", login_timeout=10, timeout=60, charset="UTF-8")
    print("OLD_ON_10", _rows(old, FLAGS))
    print("OLD_ON_19_VIA_LINK", _rows(old, f"SELECT * FROM OPENQUERY([192.168.5.19], '{FLAGS.replace(chr(39), chr(39) * 2)}')"))
    old.close()
except Exception as e:  # noqa: BLE001
    print("OLD FAILED", type(e).__name__, str(e)[:200])

# 2) the new account, straight to its server
srv = _os.environ.get("FACTORY_DB_SERVER", "")
print("NEW_SERVER", srv or "(FACTORY_DB_SERVER not set)")
try:
    new = _pymssql.connect(server=srv, port=int(_os.environ.get("FACTORY_DB_PORT", "1433")),
                           user=_os.environ["FACTORY_DB_USER"], password=_os.environ["FACTORY_DB_PASSWORD"],
                           database="master", login_timeout=10, timeout=60, charset="UTF-8")
    print("NEW_FLAGS", _rows(new, FLAGS))
    print("NEW_DB_ACCESS", {d: _rows(new, f"SELECT HAS_DBACCESS('{d}') AS ok")[0]["ok"]
                            for d in ["mi", "ESTIMATE", "MI2_ESTIMATE", "QC_MASTER", "MAINTENANCE_MACHINE"] + SHOULD_NOT})
    missing = []
    for t in NEED:
        ok = _rows(new, f"SELECT HAS_PERMS_BY_NAME('{t}', 'OBJECT', 'SELECT') AS ok")[0]["ok"]
        if ok != 1:
            missing.append(t)
    print("NEW_MISSING_SELECT", missing or "none")
    new.close()
except Exception as e:  # noqa: BLE001
    print("NEW CONNECT FAILED", type(e).__name__, str(e)[:200])

# 3) the bot's own lookups through the new account (FACTORY_DB_* → direct mode)
try:
    import factory as _fx
    import priced_jobs as _pj
    print("DIRECT_MODE", bool(_fx.FACTORY_DB_SERVER))
    today = _fx._bangkok_today()
    checks = (
        ("ESTIMATES_THIS_MONTH", lambda: _pj.search({"status": "approve", "date_from": today.replace(day=1),
                                                    "date_to": today})["count"]),
        ("JOB_J82601825", lambda: _fx.job_overview("J82601825").get("found")),
        ("DUE_7D", lambda: _fx.due_jobs({"kind": "due"}, today)["count"]),
        ("OVERDUE_30D", lambda: _fx.due_jobs({"kind": "overdue"}, today)["count"]),
        ("REPAIRS_OPEN", lambda: sum(c for s, c in _fx.repairs({"kind": "repairs", "status": "open"}, today)["counts"].items()
                                     if s in _fx.OPEN_STAGES)),
        ("QC_7D", lambda: _fx.qc_overview({"kind": "qc"}, today)["summary"].get("inspections")),
    )
    for label, fn in checks:
        t0 = _time.monotonic()
        try:
            print(label, fn(), f"({_time.monotonic() - t0:.2f}s)")
        except Exception as e:  # noqa: BLE001
            print(label, "FAILED", type(e).__name__, str(e)[:250])
except Exception as e:  # noqa: BLE001
    print("BOT FAILED", type(e).__name__, str(e)[:250])
print("DONE")
