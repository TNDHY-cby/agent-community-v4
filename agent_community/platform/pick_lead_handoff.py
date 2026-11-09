# -*- coding: utf-8 -*-
"""V142 补丁：**选完组长要把桥交到它手里并唤醒拉入**（老板 2026-11-09 追加）。

老板原话：「选中组长后得把**具体桥**给到当组长的 agent 手里，从而**唤醒、拉入**。」

## 缺的那一环

平台早就有这三件（各自独立）：
- `POST …/lead` 落名单；
- `GET …/lead-kit` 拼组长对接单（含具体桥：wakeup_method / 地址 / 桥路径 / 一次性口令）；
- `dispatch.activate()` 真触达（A 档真 POST / B 档写 `_wakeup.flag` / C 档人工，绝不假装成功）。

但它们**没串起来**：选完组长只落名单，对接单要人再去点一次，唤醒压根没做。
本补丁把这一环接上，写在 `pick_lead.handoff()`：**落盘对接单 → 写通知 → 真唤醒**。

## 三条纪律

1. 对接单**落盘到 `_lead/组长对接单.md`** —— 这才是"给到它手里"（能读文件的 agent 直接读）；
2. **同时**往 `_lead/_notice.md` 追加一行指路（短，不抢焦点）；
3. **真唤醒**走 `dispatch.activate()`，结果原样带回；**拼不出对接单也要唤醒**（唤醒不依赖全文），
   但拼装失败要如实写进响应（`kit_error`），不许静默。
"""
from pathlib import Path


def _manifest_bits(ws) -> dict:
    """取 manifest 的入口点 / 忽略区（取不到就空 —— 对接单照拼，不阻断）。"""
    try:
        from . import manifest as _mf

        wp = str(getattr(ws, "workspace_dir", "") or "")
        ok, body, _errs = _mf.read(wp)
        if ok and isinstance(body, dict):
            return {"entry_points": list(body.get("entry_points") or []),
                    "ignore_zones": list(body.get("ignore_zones") or [])}
    except Exception as e:                                     # noqa: BLE001 — 取不到就用空值，但喊一声
        print("[pick_lead] 读 manifest 失败（%s: %s）；对接单用空入口点" % (type(e).__name__, e),
              flush=True)
    return {"entry_points": [], "ignore_zones": []}


def build_kit_text(ws, *, issue_token: bool = False) -> tuple:
    """拼**组长对接单**全文 ⇒ `(text, error)`。

    ⚠️ 取值口径与 `GET /api/workshop/{id}/lead-kit` **同源**（都用 `kit.build_kit`）；
    两处的差别只有"要不要签 token"。拼不出来就返回 `("", 原因)`，**不抛**。
    """
    from . import kit as _kit
    from . import mind as _mind

    wid = str(getattr(ws, "workshop_id", "") or "")
    wp = str(getattr(ws, "workspace_dir", "") or "")
    if not wid or not wp:
        return "", "工作间缺 workshop_id / workspace_dir"
    bits = _manifest_bits(ws)
    try:
        mp = str(_mind.mind_path(f"{wid}-组长"))
        _mind.ensure(f"{wid}-组长")
    except Exception as e:                                     # noqa: BLE001 — 心智根建不出来要如实说
        return "", "心智根建不出来（%s: %s）" % (type(e).__name__, e)
    cred, note = "", ""
    if issue_token:
        try:
            from . import intake_token as _tok

            t = _tok.issue(wid, "_lead")
            cred, note = str(t["token"]), "⚠️ A 档一次性凭据（绑本间本工位、可吊销），别转贴"
        except Exception as e:                                 # noqa: BLE001 — 签不出 token 就退成"不带凭据"
            note = "凭据没签出来（%s: %s）⇒ 这份单子不含 token" % (type(e).__name__, e)
    try:
        text = _kit.build_kit(role="组长", workshop_id=wid,
                              project_short=str(getattr(ws, "name", "") or ""),
                              session_label_value="%s-组长" % wid,
                              workshop_path=wp, mind_path=mp, slot_dir="_lead",
                              notice_path="_lead/_notice.md",
                              entry_points=bits["entry_points"],
                              ignore_zones=bits["ignore_zones"],
                              member_field_value="", credential=cred, credential_note=note,
                              is_lead=True, member_seq=0)
    except Exception as e:                                     # noqa: BLE001 — 拼装失败如实说
        return "", "拼对接单失败（%s: %s）" % (type(e).__name__, e)
    return str(text or ""), ""


def handoff(ws, harness_id: str) -> dict:
    """**把桥交到组长手里并唤醒它**（`V142` 补丁）。

    做三件：① 对接单落盘 `_lead/组长对接单.md`；② `_lead/_notice.md` 追加一行指路；
    ③ `dispatch.activate()` 真唤醒（A 档真 POST / B 档写 flag / C 档人工）。
    """
    from . import dispatch as _dp
    from . import v5_roster as _rw

    wid = str(getattr(ws, "workshop_id", "") or "")
    wp = str(getattr(ws, "workspace_dir", "") or "")
    out: dict = {"ok": True, "workshop_id": wid, "harness_id": str(harness_id or "")}
    if not wp:
        return {"ok": False, "code": "NO_WORKSPACE_DIR",
                "error": "工作间没有 workspace_dir，交不了桥", "workshop_id": wid}

    # ① 对接单落盘
    text, err = build_kit_text(ws)
    if text:
        f = Path(wp) / "_lead" / "组长对接单.md"
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text, encoding="utf-8")
            out["kit"] = {"ok": True, "file": str(f), "chars": len(text),
                          "note": "对接单已落盘：能读文件的 agent 直接读这份；里面的桥/地址/口令就是给它用的"}
        except OSError as e:                                   # noqa: BLE001 — 写不进去要说清
            out["kit"] = {"ok": False, "error": "写不进去（%s: %s）" % (type(e).__name__, e)}
    else:
        out["kit"] = {"ok": False, "error": err}
        out["kit_error"] = err

    # ② 通知一行（短，指路）
    try:
        r = _dp.append_notice(wp, "_lead",
                              "组长对接单已放到 `_lead/组长对接单.md`（%d 字）—— 按它接入；"
                              "平台已尝试唤醒。" % len(text))
        out["notice"] = {"ok": bool(r.get("ok")), "file": str(r.get("file") or "")}
    except Exception as e:                                     # noqa: BLE001 — 通知写不了不影响唤醒
        out["notice"] = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}

    # ③ 真唤醒（复用派活那条触达层，绝不假装成功）
    tier = ""
    try:
        mem = _rw.effective_members(wid)
        lead = next((m for m in (mem.get("members") or [])
                     if str((m or {}).get("slot_dir") or "").strip().strip("/") == "_lead"), None)
        tier = str((lead or {}).get("tier") or "")
    except Exception as e:                                     # noqa: BLE001 — 取不到档位就按 A 档试
        print("[pick_lead] 取组长档位失败（%s: %s）" % (type(e).__name__, e), flush=True)
    try:
        wake = _dp.activate(wp, "_lead", harness_id=str(harness_id or ""), tier=tier or "a_protocol",
                            task_id="lead-kit",
                            task_rel="_lead/组长对接单.md" if text else "_lead/_notice.md",
                            report_hint="按 `_lead/组长对接单.md` 接入，进间后回报一句")
        out["wake"] = wake
        out["awake"] = str(wake.get("result") or "") in ("ok", "queued")
    except Exception as e:                                     # noqa: BLE001 — 唤醒抛错要如实说（最坏情况）
        out["wake"] = {"result": "failed", "detail": "%s: %s" % (type(e).__name__, e)}
        out["awake"] = False
    if not out.get("awake"):
        out["next"] = ("桥已放好（`_lead/组长对接单.md`），但**没唤醒成功**：%s。"
                       "可以按 `wake.detail` 修（地址/桥是否在跑），或人工把这份单子贴给它"
                       % str((out.get("wake") or {}).get("detail") or "")[:120])
    return out
