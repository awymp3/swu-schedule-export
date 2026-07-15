#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
西南大学课程表抓取器
============================================
用法:  python3 capture.py

流程:
  1. 脚本启动一个 Chrome 窗口
  2. 你在窗口里通过西南大学统一身份认证登录，再进入教务系统（验证码 / 内网 / 任何步骤都你来点）
  3. 进入「课程表 / 我的课表」页面，确认能看到完整课表
  4. 回到终端按【回车】
  5. 脚本抓取课表 DOM，写入 captured.js，并自动打开可视化应用

特点:
  • 优先使用你电脑已安装的 Chrome，无需下载 Chromium
  • 数据只写到本地文件，不上传任何服务器
  • 抓取失败时自动回退抓取整页 HTML，尽量不漏
"""

import sys
import os
import json
import webbrowser
import time

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML = os.path.join(HERE, "index.html")
CAPTURED_JS = os.path.join(HERE, "captured.js")

# 西南大学统一身份认证起始页。登录后请自行导航到「我的课表」页面再回车抓取。
DEFAULT_URL = "https://jw.swu.edu.cn/sso/zllogin"

# 课表关键选择器：用于不含标准表格视图的后备抓取
COURSE_SELECTORS = [
    "div.timetable_con",          # 你学校的课程块
    "td[id^='jc_']",              # 节次单元格
    "#kbtable", "#kbmain",        # 经典正方课表表格
    "table.kbcontent",            # 旧版正方
]


def schedule_capture_script():
    """返回浏览器端抓取脚本。

    正方新版页面同时包含「学年 / 学期」筛选表单、表格视图和列表视图。
    旧实现仅逐行抓取列表视图，因而会丢掉筛选条件，也无法读取表格视图中
    title 属性标注的完整课程信息。这里优先保留筛选表单和表格视图的完整 DOM。
    """
    return """
    () => {
      const SELS = %s;
      const selected = (id) => {
        const el = document.getElementById(id);
        const opt = el && el.options && el.options[el.selectedIndex];
        return opt ? { value: opt.value, text: (opt.textContent || '').trim() } : null;
      };
      const form = document.querySelector('#ajaxForm');
      const table = document.querySelector(
        '#ylkbTable #table1 table.timetable1, #ylkbTable #table1 table.timetable, ' +
        '#ylkbTable #kbgrid_table_0, #table1 table.timetable1, #table1 table');
      const title = table && table.querySelector('.timetable_title');
      const meta = {
        academicYear: selected('xnm'),
        term: selected('xqm'),
        timetableTitle: title ? (title.textContent || '').replace(/\\s+/g, ' ').trim() : ''
      };

      // 新版正方：抓取页面中与截图一致的「表格」视图，并附上学年/学期表单。
      if (table && table.querySelector('.timetable_con')) {
        const html = '<section id="schedule-capture">' +
          (form ? form.outerHTML : '') + table.outerHTML + '</section>';
        return {
          html,
          hit: table.querySelectorAll('.timetable_con').length,
          mode: 'timetable-grid',
          meta
        };
      }

      // 旧版正方 / 非标准页面的后备逻辑。
      const parts = [];
      const seen = new Set();
      for (const sel of SELS) {
        document.querySelectorAll(sel).forEach(el => {
          const tr = el.closest('tr') || el;
          if (seen.has(tr)) return;
          seen.add(tr);
          parts.push(tr.outerHTML);
        });
      }
      if (parts.length < 2) {
        return {
          html: document.body ? document.body.outerHTML : document.documentElement.outerHTML,
          hit: parts.length,
          mode: 'fullpage',
          meta
        };
      }
      return {
        html: '<table>' + parts.join('\\n') + '</table>',
        hit: parts.length,
        mode: 'selector',
        meta
      };
    }
    """ % json.dumps(COURSE_SELECTORS)


def log(msg):
    print(f"  {msg}", flush=True)


def banner():
    print("=" * 56)
    print("   📅  西南大学课程表抓取器")
    print("=" * 56)


def try_import_playwright():
    try:
        from playwright.sync_api import sync_playwright  # noqa
        return True
    except ImportError:
        return False


def launch_browser(p):
    """优先用系统 Chrome，失败再退回 Playwright 自带 Chromium。"""
    last_err = None
    for kwargs in ({"channel": "chrome"}, {}):  # 先系统 chrome，再内置
        try:
            browser = p.chromium.launch(headless=False, args=[
                "--start-maximized",
            ], **kwargs)
            which = "系统 Chrome" if kwargs.get("channel") else "内置 Chromium"
            log(f"已启动浏览器（{which}）")
            return browser
        except Exception as e:  # noqa
            last_err = e
            continue
    raise RuntimeError(f"无法启动浏览器：{last_err}")


def grab_courses(page):
    """在页面中抓取课表 DOM。返回 (html_片段, 命中数)。"""
    return page.evaluate(schedule_capture_script())


def grab_all_frames(page):
    """页面可能把课表放在 iframe 里，逐个 frame 尝试，挑命中最多的。"""
    best = {"html": "", "hit": -1, "mode": "none", "url": ""}
    for fr in page.frames:
        try:
            res = fr.evaluate(schedule_capture_script())
        except Exception:
            continue
        if res and res.get("hit", 0) > best["hit"]:
            res["url"] = fr.url
            best = res
    return best


def write_captured(html, meta):
    payload = {
        "html": html,
        "meta": meta,
        "capturedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    js = "window.CAPTURED_SCHEDULE = " + json.dumps(payload, ensure_ascii=False) + ";\n"
    with open(CAPTURED_JS, "w", encoding="utf-8") as f:
        f.write(js)
    log(f"已写入抓取结果 → {CAPTURED_JS}")


def open_app():
    url = "file://" + INDEX_HTML
    log(f"正在打开可视化应用 …")
    webbrowser.open(url)


def main():
    banner()
    if not try_import_playwright():
        print("\n  ❌ 未检测到 Playwright，请先安装：")
        print("     pip3 install playwright -i https://pypi.tuna.tsinghua.edu.cn/simple")
        print("     （如提示缺少浏览器，可运行：python3 -m playwright install chromium）\n")
        sys.exit(1)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = launch_browser(p)
        context = browser.new_context(no_viewport=True)
        page = context.new_page()
        try:
            page.goto(DEFAULT_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            log("（起始页加载较慢或需内网，可在地址栏手动访问，不影响后续抓取）")

        print()
        log("👉 请在弹出的 Chrome 窗口中：")
        log("   1) 通过西南大学统一身份认证登录，再进入教务系统（验证码/内网都由你操作）")
        log("   2) 进入『课程表 / 我的课表』页面，确认能看到完整课表")
        print()
        try:
            input("  ✅ 看到完整课表后，回到这里按【回车】开始抓取 …")
        except (EOFError, KeyboardInterrupt):
            print("\n  已取消。")
            browser.close()
            return

        log("正在抓取课表 …")
        # 先抓主页面，再扫所有 frame，取命中更多的
        try:
            main_res = grab_courses(page)
        except Exception as e:
            main_res = {"html": "", "hit": 0, "mode": "err", "err": str(e)}
        frame_res = grab_all_frames(page)

        chosen = main_res
        if frame_res.get("hit", 0) > main_res.get("hit", 0):
            chosen = frame_res

        hit = chosen.get("hit", 0)
        mode = chosen.get("mode", "?")
        html = chosen.get("html", "")

        if not html:
            log("⚠️ 没抓到任何内容。请确认已停留在课表页面后重试。")
            browser.close()
            return

        if mode == "fullpage":
            log(f"未精确命中课程块，已抓取整页（命中 {hit} 处），应用会尽力解析。")
        else:
            log(f"✓ 命中 {hit} 处课程相关节点。")

        meta = {"hit": hit, "mode": mode, "pageUrl": chosen.get("url") or page.url}
        meta.update(chosen.get("meta") or {})
        write_captured(html, meta)

        open_app()
        print()
        log("✅ 完成！浏览器里的可视化应用已自动载入课表。")
        log("   接下来在应用里设置『第一周日期』和『作息时间』，即可导出 .ics。")
        print()
        try:
            input("  按【回车】关闭抓取用的 Chrome 窗口（应用页面不受影响）…")
        except (EOFError, KeyboardInterrupt):
            pass
        browser.close()


if __name__ == "__main__":
    main()
