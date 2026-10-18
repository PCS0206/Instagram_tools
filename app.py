"""Instagram activity cleaner (Playwright + customtkinter).

Log in yourself in the opened browser (the program never touches your password).
The login session is kept in ./profile.
"""
import ctypes
import json
import queue
import re
import sys
import threading
import time
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

BASE = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
PROFILE = BASE / "profile"
DEBUG = BASE / "debug"
CONFIG = BASE / "config.json"
BASE_URL = "https://www.instagram.com/your_activity/interactions/"

# 인스타 UI 언어는 계정 설정을 따르므로 ko/en/ja 문구를 모두 인식한다.
SELECT_RE = re.compile(r"^\s*(선택|Select|選択)\s*$")
DELETE = r"삭제|Delete|削除"
KINDS = {  # key: (URL 경로, 실행 버튼 정규식)
    "comments": ("comments", re.compile(rf"^\s*({DELETE})\s*$")),
    "story_replies": ("story_replies", re.compile(rf"^\s*({DELETE})\s*$")),
    "likes": ("likes", re.compile(rf"^\s*(좋아요 취소|Unlike|いいね!?を取り消す|{DELETE})\s*$")),
}
CHECKBOX_SEL = '[data-testid="bulk_action_checkbox"]'

LANGS = {"ko": "한국어", "en": "English", "ja": "日本語"}
TEXT = {
    "ko": {
        "title": "인스타 활동 삭제기",
        "subtitle": "댓글 · 스토리 답장 · 좋아요를 한 번에 정리합니다",
        "targets": "삭제 대상",
        "comments": "댓글", "story_replies": "스토리 답장", "likes": "좋아요",
        "settings": "설정",
        "batch": "한 번에 처리할 개수", "delay": "배치 간격(초)", "limit": "대상별 최대 개수 (0 = 무제한)",
        "open": "1. 브라우저 열기 / 로그인", "start": "2. 시작", "stop": "중지",
        "log": "로그",
        "status_idle": "대기 중", "status_browser": "브라우저 준비됨", "status_run": "실행 중…",
        "confirm_title": "확인",
        "confirm": "{names} 항목이 삭제/취소됩니다.\n되돌릴 수 없습니다. 진행할까요?",
        "pick_one": "대상을 하나 이상 선택하세요.",
        "l_hello": "먼저 '브라우저 열기'로 로그인한 뒤 '시작'을 누르세요.",
        "l_opened": "브라우저가 열렸습니다. 로그인되어 있지 않다면 직접 로그인하세요.",
        "l_begin": "[{name}] 시작 (한 번에 {batch}개, 간격 {delay}초, 최대 {limit}개)",
        "l_limit": "[{name}] 설정한 최대 개수에 도달했습니다.",
        "l_nosel": "[{name}] '선택' 버튼을 찾지 못했습니다. 로그인 여부/남은 항목을 확인하세요.",
        "l_empty": "[{name}] 선택할 항목이 없습니다. (완료되었거나 선택자를 못 찾음)",
        "l_noact": "[{name}] 실행 버튼을 찾지 못했습니다.",
        "l_done": "[{name}] {n}개 요청 (누적 {total}개)",
        "l_end": "작업 종료.",
        "l_stop": "중지 요청됨…",
        "l_err": "오류: {e}",
        "l_berr": "브라우저 오류: {e}",
        "l_dump": "디버그 파일 저장: {path}",
    },
    "en": {
        "title": "Instagram Activity Cleaner",
        "subtitle": "Clean up comments, story replies and likes in bulk",
        "targets": "Targets",
        "comments": "Comments", "story_replies": "Story replies", "likes": "Likes",
        "settings": "Settings",
        "batch": "Items per batch", "delay": "Delay between batches (s)", "limit": "Max items per target (0 = unlimited)",
        "open": "1. Open browser / Log in", "start": "2. Start", "stop": "Stop",
        "log": "Log",
        "status_idle": "Idle", "status_browser": "Browser ready", "status_run": "Running…",
        "confirm_title": "Confirm",
        "confirm": "{names} will be deleted / removed.\nThis cannot be undone. Continue?",
        "pick_one": "Select at least one target.",
        "l_hello": "Click 'Open browser', log in, then click 'Start'.",
        "l_opened": "Browser opened. Log in manually if you are not signed in.",
        "l_begin": "[{name}] started (batch {batch}, delay {delay}s, max {limit})",
        "l_limit": "[{name}] Reached the maximum count.",
        "l_nosel": "[{name}] 'Select' button not found. Check you are logged in / items remain.",
        "l_empty": "[{name}] Nothing to select (finished, or selector not found).",
        "l_noact": "[{name}] Action button not found.",
        "l_done": "[{name}] {n} requested (total {total})",
        "l_end": "Finished.",
        "l_stop": "Stop requested…",
        "l_err": "Error: {e}",
        "l_berr": "Browser error: {e}",
        "l_dump": "Debug files saved: {path}",
    },
    "ja": {
        "title": "Instagram アクティビティ削除ツール",
        "subtitle": "コメント・ストーリーズの返信・いいねを一括で整理します",
        "targets": "削除対象",
        "comments": "コメント", "story_replies": "ストーリーズの返信", "likes": "いいね",
        "settings": "設定",
        "batch": "1回の処理件数", "delay": "バッチ間隔(秒)", "limit": "対象ごとの最大件数 (0 = 無制限)",
        "open": "1. ブラウザを開く / ログイン", "start": "2. 開始", "stop": "停止",
        "log": "ログ",
        "status_idle": "待機中", "status_browser": "ブラウザ準備完了", "status_run": "実行中…",
        "confirm_title": "確認",
        "confirm": "{names} が削除/取り消されます。\n元に戻せません。続行しますか?",
        "pick_one": "対象を1つ以上選択してください。",
        "l_hello": "「ブラウザを開く」でログインしてから「開始」を押してください。",
        "l_opened": "ブラウザを開きました。ログインしていない場合は手動でログインしてください。",
        "l_begin": "[{name}] 開始 (1回{batch}件, 間隔{delay}秒, 最大{limit})",
        "l_limit": "[{name}] 最大件数に達しました。",
        "l_nosel": "[{name}] 「選択」ボタンが見つかりません。ログイン状態/残りを確認してください。",
        "l_empty": "[{name}] 選択する項目がありません。(完了、またはセレクタ未検出)",
        "l_noact": "[{name}] 実行ボタンが見つかりません。",
        "l_done": "[{name}] {n}件を要求 (累計{total}件)",
        "l_end": "作業終了。",
        "l_stop": "停止を要求しました…",
        "l_err": "エラー: {e}",
        "l_berr": "ブラウザエラー: {e}",
        "l_dump": "デバッグファイルを保存: {path}",
    },
}


def windows_lang():
    try:
        lid = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF
        return {0x12: "ko", 0x11: "ja"}.get(lid, "en")
    except Exception:  # noqa: BLE001
        return "en"


def load_lang():
    try:
        lang = json.loads(CONFIG.read_text(encoding="utf-8")).get("lang")
        if lang in LANGS:
            return lang
    except Exception:  # noqa: BLE001
        pass
    return windows_lang()  # 처음 실행 시 윈도우 언어를 따른다


class Worker(threading.Thread):
    """Playwright는 만든 스레드에서만 써야 하므로 전용 스레드에서 명령 큐를 처리한다."""

    def __init__(self, log, on_state):
        super().__init__(daemon=True)
        self.log = log  # log(key, **kw) — 표시 시점에 현재 언어로 번역됨
        self.on_state = on_state
        self.cmds = queue.Queue()
        self.stop_flag = threading.Event()

    def run(self):
        try:
            with sync_playwright() as p:
                opts = dict(headless=False, locale="ko-KR", no_viewport=True, args=["--start-maximized"])
                try:
                    ctx = p.chromium.launch_persistent_context(str(PROFILE), **opts)
                except Exception:  # noqa: BLE001  번들 Chromium이 없으면 설치된 Edge/Chrome 사용
                    try:
                        ctx = p.chromium.launch_persistent_context(str(PROFILE), channel="msedge", **opts)
                    except Exception:  # noqa: BLE001
                        ctx = p.chromium.launch_persistent_context(str(PROFILE), channel="chrome", **opts)
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto(BASE_URL + "comments")
                self.log("l_opened")
                self.on_state("browser")
                while True:
                    cmd = self.cmds.get()
                    if cmd[0] == "close":
                        break
                    if cmd[0] == "start":
                        self.stop_flag.clear()
                        self.on_state("run")
                        try:
                            for kind in cmd[4]:
                                if self.stop_flag.is_set():
                                    break
                                self.delete_all(page, cmd[1], cmd[2], cmd[3], kind)
                        except Exception as e:  # noqa: BLE001
                            self.log("l_err", e=e)
                            self.dump(page)
                        self.log("l_end")
                        self.on_state("browser")
                ctx.close()
        except Exception as e:  # noqa: BLE001
            self.log("l_berr", e=e)
            self.on_state("idle")

    def dump(self, page):
        DEBUG.mkdir(exist_ok=True)
        try:
            page.screenshot(path=str(DEBUG / "page.png"))
            (DEBUG / "page.html").write_text(page.content(), encoding="utf-8")
            self.log("l_dump", path=DEBUG)
        except Exception:  # noqa: BLE001
            pass

    def sleep(self, sec):
        end = time.time() + sec
        while time.time() < end and not self.stop_flag.is_set():
            time.sleep(0.2)

    def find_btn(self, scope, regex):
        loc = scope.get_by_role("button", name=regex)
        if loc.count() == 0:
            loc = scope.get_by_text(regex)
        return loc.last if loc.count() else None

    @staticmethod
    def snap(h):
        try:
            return h.evaluate("e => e.parentElement.parentElement.outerHTML")
        except Exception:  # noqa: BLE001
            return None

    def click_box(self, h):
        try:
            h.scroll_into_view_if_needed(timeout=3000)
            h.click(timeout=3000, force=True)  # pointer-events:none 라 좌표 클릭
            self.sleep(0.15)
            return True
        except PWTimeout:
            return False

    def select_items(self, boxes, want):
        """체크박스 요소를 한 번씩만 클릭하고, 선택 상태가 실제로 바뀐 개수를 돌려준다."""
        handles = boxes.element_handles()[:want]  # nth(i) 대신 핸들 고정 → 목록이 밀려도 같은 항목을 두 번 누르지 않음
        before = [self.snap(h) for h in handles]
        clicked = []
        for i, h in enumerate(handles):
            if self.stop_flag.is_set():
                break
            if self.click_box(h):
                clicked.append(i)
        changed = [i for i in clicked if self.snap(handles[i]) != before[i]]
        if not changed:  # 상태 변화를 감지할 수 없는 구조면 클릭 수를 그대로 신뢰
            return len(clicked)
        for i in clicked:  # 일부만 바뀌었다면 안 바뀐 것만 한 번 재시도
            if i not in changed and self.click_box(handles[i]) and self.snap(handles[i]) != before[i]:
                changed.append(i)
        return len(changed)

    def delete_all(self, page, batch, delay, limit, kind):
        path, action_re = KINDS[kind]
        name = f"@{kind}"  # '@' 접두 → 표시 시점에 번역
        url = BASE_URL + path
        deleted = 0
        self.log("l_begin", name=name, batch=batch, delay=delay, limit=limit or "∞")
        while not self.stop_flag.is_set():
            if limit and deleted >= limit:
                self.log("l_limit", name=name)
                return
            if not page.url.startswith(url):
                page.goto(url)
            page.wait_for_load_state("domcontentloaded")
            self.sleep(2.5)

            sel = self.find_btn(page, SELECT_RE)
            if sel is None:
                self.log("l_nosel", name=name)
                self.dump(page)
                return
            sel.click()
            self.sleep(1)

            boxes = page.locator(CHECKBOX_SEL)
            if boxes.count() == 0:
                self.log("l_empty", name=name)
                self.dump(page)
                return

            want = min(batch, limit - deleted) if limit else batch
            clicked = self.select_items(boxes, want)
            if clicked == 0 or self.stop_flag.is_set():
                page.reload()
                continue

            btn = self.find_btn(page, action_re)
            if btn is None:
                self.log("l_noact", name=name)
                self.dump(page)
                return
            btn.click()
            self.sleep(1)
            dlg = page.locator('[role="dialog"]')
            if dlg.count():
                confirm = self.find_btn(dlg.last, action_re)
                if confirm is not None:
                    confirm.click()
            deleted += clicked
            self.log("l_done", name=name, n=clicked, total=deleted)
            self.sleep(delay)
            page.goto(url)


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")
        self.lang = load_lang()
        self.geometry("620x720")
        self.minsize(560, 640)
        self.worker = None
        self.logq = queue.Queue()
        self.ui_state = "idle"
        self.entries = []  # (시각, key, kw) — 언어 변경 시 로그를 다시 그리기 위해 보관
        self.i18n = []  # (widget, key) — 언어가 바뀔 때 텍스트 갱신

        self.build()
        self.apply_lang()
        self.log("l_hello")
        self.after(150, self.pump)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------- i18n ----------
    def t(self, key, **kw):
        s = TEXT[self.lang][key]
        if kw:
            kw = {k: (self.t(v[1:]) if isinstance(v, str) and v.startswith("@") else v) for k, v in kw.items()}
            s = s.format(**kw)
        return s

    def reg(self, widget, key):
        self.i18n.append((widget, key))
        return widget

    def apply_lang(self):
        self.title(self.t("title"))
        for w, key in self.i18n:
            w.configure(text=self.t(key))
        self.set_state(self.ui_state)
        self.render_log()
        try:
            CONFIG.write_text(json.dumps({"lang": self.lang}), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    def change_lang(self, label):
        self.lang = next(k for k, v in LANGS.items() if v == label)
        self.apply_lang()

    # ---------- UI ----------
    def card(self, title_key):
        frame = ctk.CTkFrame(self, corner_radius=14)
        frame.pack(fill="x", padx=20, pady=(0, 12))
        self.reg(ctk.CTkLabel(frame, text="", font=ctk.CTkFont(size=13, weight="bold"), anchor="w"), title_key).pack(
            fill="x", padx=16, pady=(12, 6))
        return frame

    def build(self):
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=20, pady=(18, 12))
        titles = ctk.CTkFrame(head, fg_color="transparent")
        titles.pack(side="left", fill="x", expand=True)
        self.reg(ctk.CTkLabel(titles, text="", font=ctk.CTkFont(size=22, weight="bold"), anchor="w"), "title").pack(fill="x")
        self.reg(ctk.CTkLabel(titles, text="", text_color=("gray40", "gray65"), anchor="w"), "subtitle").pack(fill="x")
        self.lang_menu = ctk.CTkSegmentedButton(head, values=list(LANGS.values()), command=self.change_lang)
        self.lang_menu.set(LANGS[self.lang])
        self.lang_menu.pack(side="right")

        c = self.card("targets")
        row = ctk.CTkFrame(c, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(0, 14))
        self.kind_vars = {}
        for k in KINDS:
            v = ctk.BooleanVar(value=(k == "comments"))
            self.reg(ctk.CTkCheckBox(row, text="", variable=v), k).pack(side="left", padx=(0, 18))
            self.kind_vars[k] = v

        c = self.card("settings")
        self.batch = ctk.StringVar(value="20")
        self.delay = ctk.StringVar(value="8")
        self.limit = ctk.StringVar(value="0")
        for key, var in (("batch", self.batch), ("delay", self.delay), ("limit", self.limit)):
            r = ctk.CTkFrame(c, fg_color="transparent")
            r.pack(fill="x", padx=16, pady=(0, 8))
            self.reg(ctk.CTkLabel(r, text="", anchor="w"), key).pack(side="left")
            ctk.CTkEntry(r, textvariable=var, width=80, justify="center").pack(side="right")
        ctk.CTkFrame(c, height=4, fg_color="transparent").pack()

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=20, pady=(0, 6))
        self.b_open = self.reg(ctk.CTkButton(btns, text="", height=40, command=self.open_browser), "open")
        self.b_start = self.reg(ctk.CTkButton(btns, text="", height=40, fg_color="#2e9f5b", hover_color="#237a46",
                                              command=self.start), "start")
        self.b_stop = self.reg(ctk.CTkButton(btns, text="", height=40, width=90, fg_color="#d04444",
                                             hover_color="#a33333", command=self.stop), "stop")
        self.b_open.pack(side="left", expand=True, fill="x", padx=(0, 8))
        self.b_start.pack(side="left", expand=True, fill="x", padx=(0, 8))
        self.b_stop.pack(side="left")

        self.status = ctk.CTkLabel(self, text="", anchor="w", text_color=("gray40", "gray65"))
        self.status.pack(fill="x", padx=24, pady=(4, 2))

        c = self.card("log")
        c.pack_configure(fill="both", expand=True)
        self.out = ctk.CTkTextbox(c, state="disabled", font=ctk.CTkFont(family="Consolas", size=12), corner_radius=8)
        self.out.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    def set_state(self, state):
        self.ui_state = state
        key = {"idle": "status_idle", "browser": "status_browser", "run": "status_run"}[state]
        self.status.configure(text="● " + self.t(key))
        self.b_open.configure(state="normal" if state == "idle" else "disabled")
        self.b_start.configure(state="normal" if state == "browser" else "disabled")
        self.b_stop.configure(state="normal" if state == "run" else "disabled")

    # ---------- 로그 / 스레드 연결 ----------
    def log(self, key, **kw):  # 워커 스레드에서도 호출됨 → 큐로 넘김
        self.logq.put(("log", key, kw))

    def on_state(self, state):
        self.logq.put(("state", state, None))

    def pump(self):
        while not self.logq.empty():
            kind, a, b = self.logq.get()
            if kind == "state":
                self.set_state(a)
                continue
            self.entries.append((time.strftime("%H:%M:%S"), a, b))
            self.render_log()
        self.after(150, self.pump)

    def render_log(self):
        if not hasattr(self, "out"):
            return
        self.out.configure(state="normal")
        self.out.delete("1.0", "end")
        for ts, key, kw in self.entries:
            self.out.insert("end", f"[{ts}] {self.t(key, **kw)}\n")
        self.out.see("end")
        self.out.configure(state="disabled")

    # ---------- 동작 ----------
    def open_browser(self):
        if self.worker and self.worker.is_alive():
            return
        self.worker = Worker(self.log, self.on_state)
        self.worker.start()
        self.b_open.configure(state="disabled")

    @staticmethod
    def to_num(var, default, cast=int, minimum=0):
        try:
            return max(minimum, cast(float(var.get())))
        except ValueError:
            return default

    def start(self):
        kinds = [k for k, v in self.kind_vars.items() if v.get()]
        if not kinds:
            messagebox.showinfo(self.t("title"), self.t("pick_one"))
            return
        names = ", ".join(self.t(k) for k in kinds)
        if not messagebox.askyesno(self.t("confirm_title"), self.t("confirm", names=names)):
            return
        self.worker.cmds.put(("start", self.to_num(self.batch, 20, minimum=1),
                              self.to_num(self.delay, 8.0, float), self.to_num(self.limit, 0), kinds))

    def stop(self):
        self.worker.stop_flag.set()
        self.log("l_stop")

    def on_close(self):
        if self.worker and self.worker.is_alive():
            self.worker.stop_flag.set()
            self.worker.cmds.put(("close",))
            self.worker.join(timeout=5)
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
