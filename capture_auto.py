#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
西南大学课程表自动抓取器
=================================================
反检测登录方案：undetected_chromedriver + ddddocr 验证码识别

流程（全自动）：
  1. 首次运行输入西南大学统一身份认证账号和密码（可勾选记住，本地保存到 .env）
  2. 自动检测系统/Chrome版本，下载匹配的 chromedriver（淘宝镜像）
  3. 启动反检测 Chrome，自动登录（OCR 验证码，失败自动重试）
  4. 跳转西南大学个人课表页，选择学年/学期，抓取完整表格课表 DOM → captured.js
  5. 打开可视化应用 index.html（自动载入课表）

支持: macOS (Apple/Intel) · Windows · Linux
用法:  python3 capture_auto.py
"""

import os
import sys
import ssl
import time
import json
import zipfile
import subprocess
import platform
import webbrowser
import urllib.request

# 关闭 SSL 证书校验（避免本机缺少 CA 证书导致请求失败）
ssl._create_default_https_context = ssl._create_unverified_context

# Pillow 兼容补丁：新版 Pillow(10+) 移除了 Image.ANTIALIAS，而 ddddocr 仍在用它
# 必须在 import ddddocr 之前执行
from PIL import Image as _PILImage
if not hasattr(_PILImage, "ANTIALIAS"):
    _PILImage.ANTIALIAS = _PILImage.LANCZOS

import undetected_chromedriver as uc
import ddddocr
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ================= 配置 =================
HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML = os.path.join(HERE, "index.html")
CAPTURED_JS = os.path.join(HERE, "captured.js")
ENV_FILE = os.path.join(HERE, ".env")                # 本地保存统一身份认证信息（首次输入后）
LEGACY_ACCOUNT_FILE = os.path.join(HERE, "account.json")
DRIVER_DIR = os.path.join(HERE, "drivers")          # 各平台 chromedriver 缓存目录

# [代理] 自动探测：用真实 HTTP 请求验证代理是否可用，否则直连
PROXY = "127.0.0.1:7897"

# [网址]（默认西南大学；其他学校改这两个网址即可）
URL_START = "https://jw.swu.edu.cn/sso/zllogin"
URL_KEBIAO = "https://jw.swu.edu.cn/jwglxt/kbcx/xskbcx_cxXskbcxIndex.html?gnmkdm=N2151&layout=default"

# [登录页元素 ID]（正方通用）
ID_USER = "loginName"
ID_PWD = "password"
ID_CODE = "validateCode"
ID_IMG = "kaptchaImage"
ID_TISHI = "tishi"

# 运行时填充的账号（来自 .env 或弹窗输入）
USERNAME = ""
PASSWORD = ""


def _proxy_works(hostport):
    proxy = f"http://{hostport}"
    handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    opener = urllib.request.build_opener(handler)
    try:
        req = urllib.request.Request(URL_START, headers={"User-Agent": "Mozilla/5.0"})
        opener.open(req, timeout=4)
        return True
    except Exception:
        return False


def setup_proxy():
    """探测并设置代理；本地连接不走代理。返回是否启用代理。"""
    for k in ("http_proxy", "https_proxy", "all_proxy",
              "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        os.environ.pop(k, None)
    if _proxy_works(PROXY):
        os.environ["http_proxy"] = f"http://{PROXY}"
        os.environ["https_proxy"] = f"http://{PROXY}"
        os.environ["all_proxy"] = f"socks5://{PROXY}"
        # 本地 chromedriver 通信不走代理，否则 SSL EOF
        os.environ["no_proxy"] = "localhost,127.0.0.1,::1"
        os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1"
        return True
    return False


# ================= 日志 =================
class C:
    CYAN = '\033[96m'; GREEN = '\033[92m'; YELLOW = '\033[93m'
    RED = '\033[91m'; HEADER = '\033[95m'; BOLD = '\033[1m'; ENDC = '\033[0m'

def log(msg, level="INFO"):
    t = time.strftime('%H:%M:%S')
    color = {"INFO": C.CYAN, "SUCCESS": C.GREEN, "WARN": C.YELLOW,
             "ERROR": C.RED, "SYSTEM": C.HEADER}.get(level, C.ENDC)
    print(f"{C.BOLD}[{t}]{C.ENDC} {color}{msg}{C.ENDC}", flush=True)


def force_kill_chrome():
    log("清理残留 Chrome 进程...", "SYSTEM")
    sysname = platform.system().lower()
    try:
        if "darwin" in sysname or "linux" in sysname:
            os.system("pkill -9 -f 'Google Chrome' 2>/dev/null")
            os.system("pkill -9 -f 'chromedriver' 2>/dev/null")
        elif "windows" in sysname:
            os.system("taskkill /F /IM chrome.exe /T >nul 2>&1")
            os.system("taskkill /F /IM chromedriver.exe /T >nul 2>&1")
    except Exception:
        pass


# ===== 跨平台 chromedriver 准备 =====
IS_MAC = platform.system().lower() == "darwin"
IS_WIN = platform.system().lower() == "windows"


class MacFixPatcher(uc.Patcher):
    """Mac 上 uc 在线 patch 会破坏签名导致 driver 被 -9 杀，patch 后重新签名。"""
    def auto(self, *args, **kwargs):
        result = super().auto(*args, **kwargs)
        if IS_MAC:
            try:
                path = self.executable_path
                os.system(f"xattr -c '{path}' 2>/dev/null")
                os.chmod(path, 0o755)
                subprocess.run(f"codesign --force --deep --sign - '{path}'",
                               shell=True, capture_output=True)
            except Exception:
                pass
        return result
uc.Patcher = MacFixPatcher


def detect_chrome_version():
    """检测本机 Chrome 主版本号。失败返回 None（让 uc 自行处理）。"""
    sysname = platform.system().lower()
    try:
        if "darwin" in sysname:
            out = subprocess.check_output(
                ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "--version"],
                stderr=subprocess.DEVNULL).decode()
        elif "windows" in sysname:
            # 注册表查 Chrome 版本
            out = subprocess.check_output(
                'reg query "HKEY_CURRENT_USER\\Software\\Google\\Chrome\\BLBeacon" /v version',
                shell=True, stderr=subprocess.DEVNULL).decode()
        else:
            for exe in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
                try:
                    out = subprocess.check_output([exe, "--version"],
                                                  stderr=subprocess.DEVNULL).decode()
                    break
                except Exception:
                    continue
            else:
                return None
        import re
        m = re.search(r"(\d+)\.\d+\.\d+", out)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def platform_tag():
    """返回 (chrome-for-testing 平台目录名, driver 可执行文件名)。"""
    sysname = platform.system().lower()
    machine = platform.machine().lower()
    if "darwin" in sysname:
        return ("mac-arm64" if machine in ("arm64", "aarch64") else "mac-x64", "chromedriver")
    if "windows" in sysname:
        return ("win64" if machine.endswith("64") else "win32", "chromedriver.exe")
    return ("linux64", "chromedriver")


def _mirror_versions():
    """从淘宝镜像列出全部可用版本（用于挑选最接近的）。"""
    url = "https://registry.npmmirror.com/-/binary/chrome-for-testing/"
    try:
        data = urllib.request.urlopen(url, timeout=15).read().decode()
        import re
        return re.findall(r'"name":"(\d+\.\d+\.\d+\.\d+)/"', data)
    except Exception:
        return []


def _pick_version(major):
    """挑选与本机 Chrome 主版本号匹配、且 build 号最大的镜像版本。"""
    vers = [v for v in _mirror_versions() if v.split(".")[0] == str(major)]
    if not vers:
        return None
    vers.sort(key=lambda v: [int(x) for x in v.split(".")])
    return vers[-1]


def download_driver(major):
    """下载匹配本机平台与 Chrome 版本的 chromedriver，返回可执行路径。"""
    tag, exe_name = platform_tag()
    os.makedirs(DRIVER_DIR, exist_ok=True)
    dest = os.path.join(DRIVER_DIR, f"{tag}-{major}-{exe_name}")
    if os.path.exists(dest) and os.path.getsize(dest) > 1_000_000:
        return dest  # 已缓存

    full = _pick_version(major)
    if not full:
        log(f"镜像上未找到 Chrome {major} 对应的 driver", "WARN")
        return None
    url = (f"https://registry.npmmirror.com/-/binary/chrome-for-testing/"
           f"{full}/{tag}/chromedriver-{tag}.zip")
    log(f"下载 chromedriver {full} ({tag}) ...", "INFO")
    zip_path = os.path.join(DRIVER_DIR, "cd.zip")
    try:
        urllib.request.urlretrieve(url, zip_path)
    except Exception as e:
        log(f"下载失败: {e}", "ERROR")
        return None

    # 解压，取出 chromedriver 可执行文件（排除 LICENSE.chromedriver 等同后缀文件）
    extracted = False
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            base = name.rsplit("/", 1)[-1]
            if base == exe_name:  # 精确匹配文件名，而非 endswith
                with z.open(name) as src, open(dest, "wb") as out:
                    out.write(src.read())
                extracted = True
                break
    try:
        os.remove(zip_path)
    except Exception:
        pass
    if not extracted or not os.path.exists(dest):
        log("解压未找到 chromedriver", "ERROR")
        return None
    if not IS_WIN:
        os.chmod(dest, 0o755)
    log("chromedriver 下载完成", "SUCCESS")
    return dest


def prepare_driver(major):
    """下载（或复用缓存）+ Mac 去隔离签名，返回可用的 driver 路径；失败返回 None。"""
    path = download_driver(major)
    if not path:
        return None
    if IS_MAC:
        os.system(f"xattr -c '{path}' 2>/dev/null")
        subprocess.run(f"codesign --force --deep --sign - '{path}'",
                       shell=True, capture_output=True)
    return path


# ================= 统一身份认证信息管理 =================
ENV_USER_KEY = "SWU_UNIFIED_ID_USERNAME"
ENV_PASSWORD_KEY = "SWU_UNIFIED_ID_PASSWORD"
# 兼容升级前已经写入 .env 的键名，读取后自动迁移为上面的新键名。
LEGACY_ENV_USER_KEY = "ZHENGFANG_USERNAME"
LEGACY_ENV_PASSWORD_KEY = "ZHENGFANG_PASSWORD"


def read_env_file(path):
    """读取简单的 .env 文件；支持由 json.dumps 写出的带引号值。"""
    values = {}
    if not os.path.exists(path):
        return values
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, raw = line.split("=", 1)
                key, raw = key.strip(), raw.strip()
                if not key:
                    continue
                if raw.startswith('"'):
                    try:
                        values[key] = json.loads(raw)
                        continue
                    except Exception:
                        pass
                values[key] = raw.strip("'\"")
    except Exception:
        return {}
    return values


def write_env_values(updates):
    """仅更新统一身份认证键，保留 .env 内用户自行添加的其他配置。"""
    lines = []
    seen = set()
    if os.path.exists(ENV_FILE):
        try:
            with open(ENV_FILE, "r", encoding="utf-8") as f:
                for line in f.read().splitlines():
                    key = line.split("=", 1)[0].strip() if "=" in line else ""
                    if key in updates:
                        lines.append(f"{key}={json.dumps(updates[key], ensure_ascii=False)}")
                        seen.add(key)
                    else:
                        lines.append(line)
        except Exception:
            lines = []
    for key, value in updates.items():
        if key not in seen:
            lines.append(f"{key}={json.dumps(value, ensure_ascii=False)}")
    with open(ENV_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines).rstrip()+"\n")
    # 尽量收紧权限（Windows 会忽略该设置，仍可正常使用）。
    try:
        os.chmod(ENV_FILE, 0o600)
    except Exception:
        pass


def load_account():
    """优先读取 .env；兼容旧键名和旧 account.json，并自动迁移。"""
    env = read_env_file(ENV_FILE)
    username = os.environ.get(ENV_USER_KEY) or env.get(ENV_USER_KEY)
    password = os.environ.get(ENV_PASSWORD_KEY) or env.get(ENV_PASSWORD_KEY)
    if username and password:
        return username, password

    # 一次性兼容升级前的 .env，避免改名后要求用户再次输入统一身份认证信息。
    legacy_username = os.environ.get(LEGACY_ENV_USER_KEY) or env.get(LEGACY_ENV_USER_KEY)
    legacy_password = os.environ.get(LEGACY_ENV_PASSWORD_KEY) or env.get(LEGACY_ENV_PASSWORD_KEY)
    if legacy_username and legacy_password:
        if env.get(LEGACY_ENV_USER_KEY) and env.get(LEGACY_ENV_PASSWORD_KEY):
            write_env_values({ENV_USER_KEY: legacy_username, ENV_PASSWORD_KEY: legacy_password})
            log("已将旧 .env 中的登录信息迁移为统一身份认证配置", "SUCCESS")
        return legacy_username, legacy_password

    # 一次性兼容更早版本的数据，避免升级后要求用户再次输入统一身份认证信息。
    if os.path.exists(LEGACY_ACCOUNT_FILE):
        try:
            with open(LEGACY_ACCOUNT_FILE, "r", encoding="utf-8") as f:
                legacy = json.load(f)
            username, password = legacy.get("username"), legacy.get("password")
            if username and password:
                write_env_values({ENV_USER_KEY: username, ENV_PASSWORD_KEY: password})
                log("已将旧 account.json 中的登录信息迁移到 .env", "SUCCESS")
                return username, password
        except Exception:
            pass
    return None, None


def save_account(username, password):
    try:
        write_env_values({ENV_USER_KEY: username, ENV_PASSWORD_KEY: password})
        log("统一身份认证信息已保存到本地 .env（下次免输入）", "SUCCESS")
    except Exception as e:
        log(f"登录信息保存失败: {e}", "WARN")


def prompt_account_gui():
    """弹出图形窗口让用户输入统一身份认证信息。返回 (username, password, remember)。
    无图形环境时回退到终端输入。"""
    try:
        import tkinter as tk
        from tkinter import messagebox
    except Exception:
        return prompt_account_cli()

    result = {"u": None, "p": None, "remember": True, "ok": False}

    root = tk.Tk()
    root.title("西南大学统一身份认证")
    root.resizable(False, False)
    W, H = 360, 230
    root.update_idletasks()
    x = (root.winfo_screenwidth() - W) // 2
    y = (root.winfo_screenheight() - H) // 2
    root.geometry(f"{W}x{H}+{x}+{y}")
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass

    pad = {"padx": 18}
    tk.Label(root, text="请输入西南大学统一身份认证账号", font=("", 14, "bold")).pack(pady=(18, 4))
    tk.Label(root, text="登录信息仅保存在本机 .env，用于自动登录", fg="#888", font=("", 10)).pack(pady=(0, 10))

    frm = tk.Frame(root); frm.pack(fill="x", **pad)
    tk.Label(frm, text="账号", width=5, anchor="e").grid(row=0, column=0, pady=5)
    e_user = tk.Entry(frm, width=26); e_user.grid(row=0, column=1, pady=5, padx=6)
    tk.Label(frm, text="密码", width=5, anchor="e").grid(row=1, column=0, pady=5)
    e_pwd = tk.Entry(frm, width=26, show="•"); e_pwd.grid(row=1, column=1, pady=5, padx=6)

    var_remember = tk.BooleanVar(value=True)
    tk.Checkbutton(root, text="记住登录信息（下次免输入）", variable=var_remember).pack(pady=4)

    def submit():
        u, p = e_user.get().strip(), e_pwd.get().strip()
        if not u or not p:
            messagebox.showwarning("提示", "请填写统一身份认证账号和密码")
            return
        result.update(u=u, p=p, remember=var_remember.get(), ok=True)
        # 先隐藏窗口，再退出事件循环。某些 Windows Tk 版本在回调内直接
        # destroy() 后仍会把顶层窗口留在前台一小段时间。
        root.withdraw()
        root.quit()

    def cancel():
        root.withdraw()
        root.quit()

    tk.Button(root, text="登录", width=12, command=submit).pack(pady=8)
    e_user.focus_set()
    root.bind("<Return>", lambda e: submit())
    root.protocol("WM_DELETE_WINDOW", cancel)
    root.mainloop()
    try:
        root.destroy()
    except Exception:
        pass

    if not result["ok"]:
        return None, None, False
    return result["u"], result["p"], result["remember"]


def prompt_account_cli():
    """终端输入回退方案。"""
    import getpass
    print("\n请输入西南大学统一身份认证账号（仅保存在本机 .env）：")
    u = input("  统一认证账号: ").strip()
    p = getpass.getpass("  密码: ").strip()
    ans = input("  记住登录信息？下次免输入 [Y/n]: ").strip().lower()
    return (u or None), (p or None), (ans != "n")


def resolve_account():
    """优先用本地保存的统一身份认证信息；没有则输入并按需保存。"""
    global USERNAME, PASSWORD
    u, p = load_account()
    if u and p:
        log("已加载本地统一身份认证信息", "SUCCESS")
        USERNAME, PASSWORD = u, p
        return True
    u, p, remember = prompt_account_gui()
    if not u or not p:
        log("未输入统一身份认证信息，已取消", "WARN")
        return False
    USERNAME, PASSWORD = u, p
    if remember:
        save_account(u, p)
    return True


# ================= 登录 =================
# 全局 OCR 实例（兼容不同 ddddocr 版本：新版无 show_ad 参数）
def _make_ocr():
    try:
        return ddddocr.DdddOcr(show_ad=False)
    except TypeError:
        try:
            return ddddocr.DdddOcr()
        except Exception as e:
            log(f"ddddocr 初始化失败: {e}", "ERROR")
            return None
_OCR = _make_ocr()


def get_captcha(driver):
    try:
        img = WebDriverWait(driver, 5).until(
            EC.visibility_of_element_located((By.ID, ID_IMG)))
        size = img.size
        if size['width'] == 0:
            log(f"验证码图片宽度为0，可能未加载（id={ID_IMG}）", "WARN")
            return ""
        png = img.screenshot_as_png
        if _OCR is None:
            return ""
        raw = _OCR.classification(png)
        # 只保留字母数字，正方验证码通常是 4 位
        code = "".join(ch for ch in raw if ch.isalnum())
        log(f"OCR 识别: 原始'{raw}' → 清洗'{code}' (图片 {size['width']}x{size['height']})", "INFO")
        return code
    except Exception as e:
        log(f"验证码获取异常: {e}", "WARN")
        return ""


def login_procedure(driver):
    wait = WebDriverWait(driver, 15)
    try:
        driver.get(URL_START)
        time.sleep(3)

        # 有些入口需要先点「登录」div
        try:
            btn = wait.until(EC.element_to_be_clickable(
                (By.XPATH, "//div[contains(@onclick, '_goLogin')]")))
            driver.execute_script("arguments[0].click();", btn)
        except Exception:
            pass
        time.sleep(3)

        code = get_captcha(driver)
        if len(code) != 4:
            log("验证码位数不对，刷新重试", "WARN")
            driver.refresh()
            return False

        log("填写统一身份认证信息...", "INFO")
        driver.execute_script(f"document.getElementById('{ID_USER}').value=arguments[0];", USERNAME)
        driver.execute_script(f"document.getElementById('{ID_PWD}').value=arguments[0];", PASSWORD)
        driver.execute_script(f"document.getElementById('{ID_CODE}').value=arguments[0];", code)

        driver.execute_script("verifyCode();")
        time.sleep(1)

        try:
            tishi_src = driver.find_element(By.ID, ID_TISHI).get_attribute("src") or ""
            if "code_error" in tishi_src:
                log("验证码被拒绝", "WARN")
                driver.refresh()
                return False
        except Exception:
            pass

        log("提交登录请求...", "INFO")
        driver.execute_script("portalLogin();")
        time.sleep(5)

        if "login" not in driver.current_url:
            log("登录成功！", "SUCCESS")
            return True
        log("登录未跳转，可能密码/验证码错误", "WARN")
        return False
    except Exception as e:
        log(f"登录流程出错: {e}", "ERROR")
        return False


# ================= 学年 / 学期选择与课表抓取 =================
# 仅用于旧版或非标准页面的后备抓取。新版正方优先读取表格视图（#table1）。
COURSE_SELECTORS = ["div.timetable_con", "td[id^='jc_']", "#kbtable", "table.kbcontent"]


def get_term_info(driver):
    """读取真实 select 及其全部选项，而不是 Chosen 插件可能滞后的显示文字。"""
    return driver.execute_script("""
    function getSelect(id) {
      var el = document.getElementById(id) || document.querySelector('select[name="' + id + '"]');
      var chosen = document.getElementById(id + '_chosen');
      if (!el && !chosen) return null;
      var options = Array.prototype.map.call((el && el.options) || [], function (o) {
        return {value: o.value, text: (o.textContent || '').trim()};
      }).filter(function (o) { return o.value && o.text; });
      // 正方会延后填充原生 select。若它尚未可用，退回 Chosen 的选项文本，
      // 这样用户至少能看到可选择的学年和学期。
      if (!options.length && chosen) {
        options = Array.prototype.map.call(
          chosen.querySelectorAll('.chosen-results li[data-option-array-index]'),
          function (li) { return {value: '', text: (li.textContent || '').trim()}; }
        ).filter(function (o) { return o.text && o.text !== '---请选择---'; });
      }
      var selected = el && el.options && el.options[el.selectedIndex];
      var chosenText = chosen && chosen.querySelector('.chosen-spanText');
      return {
        value: selected ? selected.value : '',
        text: selected ? (selected.textContent || '').trim() :
          (chosenText ? (chosenText.textContent || '').trim() : ''),
        options: options
      };
    }
    var title = document.querySelector('#ylkbTable #table1 .timetable_title');
    return {
      academicYear: getSelect('xnm'),
      term: getSelect('xqm'),
      timetableTitle: title ? (title.textContent || '').replace(/\\s+/g, ' ').trim() : ''
    };
    """) or {}


def wait_for_term_options(driver, timeout=12):
    """等待正方页面异步填充学年/学期下拉框；超时仍返回最后一次读取结果。"""
    deadline = time.time() + timeout
    latest = {}
    while time.time() < deadline:
        latest = get_term_info(driver)
        year = latest.get("academicYear") or {}
        term = latest.get("term") or {}
        if len(year.get("options", [])) > 1 and len(term.get("options", [])) > 1:
            return latest
        time.sleep(0.4)
    return latest or get_term_info(driver)


def option_texts(options):
    """把页面原有的选项完整显示给用户，不截断历史学年。"""
    return "、".join(o.get("text", "") for o in options if o.get("text")) or "（页面暂未提供选项）"


def set_select_by_label(driver, element_id, wanted):
    """按显示文字（或底层 value）设置下拉框，并同步 Chosen 外观。"""
    return driver.execute_script("""
    var id = arguments[0], wanted = String(arguments[1]).trim();
    var el = document.getElementById(id);
    if (!el) return {ok: false, reason: '未找到下拉框'};
    var option = Array.prototype.find.call(el.options, function (o) {
      return (o.textContent || '').trim() === wanted || o.value === wanted;
    });
    if (!option) return {ok: false, reason: '不存在该选项'};
    el.value = option.value;
    el.dispatchEvent(new Event('change', {bubbles: true}));
    if (window.jQuery) window.jQuery(el).trigger('chosen:updated');
    return {ok: true, value: option.value, text: (option.textContent || '').trim()};
    """, element_id, wanted)


def choose_academic_term(driver):
    """让用户按需选择历史/未来学期；直接回车则保留页面当前学期。"""
    info = wait_for_term_options(driver)
    year = info.get("academicYear") or {}
    term = info.get("term") or {}
    if not year or not term:
        log("页面没有标准的学年/学期下拉框，将抓取当前课表", "WARN")
        return info

    original_year = year.get("value")
    original_term = term.get("value")
    print("\n  选择要导出的课表（直接回车保留当前选择）：")
    print(f"  当前：{year.get('text') or '未选择'} 学年，第 {term.get('text') or '未选择'} 学期")
    print(f"  可选学年：{option_texts(year.get('options', []))}")
    try:
        wanted_year = input(f"  学年 [{year.get('text', '')}]: ").strip()
        if wanted_year:
            result = set_select_by_label(driver, "xnm", wanted_year)
            if not result.get("ok"):
                log(f"学年“{wanted_year}”不可用，保留当前选择", "WARN")
            else:
                log(f"已选择学年：{result['text']}", "SUCCESS")
                # 学期选项与学年关联，切换后等待页面重新填充，再读取一次。
                info = wait_for_term_options(driver)
                term = info.get("term") or {}
        else:
            info = get_term_info(driver)
            term = info.get("term") or term

        print(f"  可选学期：{option_texts(term.get('options', []))}")
        wanted_term = input(f"  学期 [{term.get('text', '')}]: ").strip()
        if wanted_term:
            result = set_select_by_label(driver, "xqm", wanted_term)
            if not result.get("ok"):
                log(f"学期“{wanted_term}”不可用，保留当前选择", "WARN")
            else:
                log(f"已选择学期：第 {result['text']} 学期", "SUCCESS")
    except (EOFError, KeyboardInterrupt):
        print()
        log("未输入学年/学期，保留页面当前选择", "INFO")
        return get_term_info(driver)

    updated = get_term_info(driver)
    changed = ((updated.get("academicYear") or {}).get("value") != original_year or
               (updated.get("term") or {}).get("value") != original_term)
    if changed:
        log("正在查询所选学年/学期的课表...", "INFO")
        driver.execute_script("""
        var btn = document.getElementById('search_go');
        if (btn) btn.click();
        """)
        # 查询由正方页面以 Ajax 刷新；给表格和课程详情留出稳定时间。
        time.sleep(3)
    return get_term_info(driver)


def grab_schedule_html(driver):
    """优先抓取与正方「表格」视图一致的完整 DOM，保留所有课程字段。"""
    js = """
    var SELS = arguments[0];
    function selected(id) {
      var el = document.getElementById(id);
      var opt = el && el.options && el.options[el.selectedIndex];
      return opt ? {value: opt.value, text: (opt.textContent || '').trim()} : null;
    }
    var form = document.querySelector('#ajaxForm');
    var table = document.querySelector(
      '#ylkbTable #table1 table.timetable1, #ylkbTable #table1 table.timetable, ' +
      '#ylkbTable #kbgrid_table_0, #table1 table.timetable1, #table1 table');
    var title = table && table.querySelector('.timetable_title');
    var meta = {
      academicYear: selected('xnm'),
      term: selected('xqm'),
      timetableTitle: title ? (title.textContent || '').replace(/\\s+/g, ' ').trim() : ''
    };
    if (table && table.querySelector('.timetable_con')) {
      return {
        html: '<section id="schedule-capture">' +
          (form ? form.outerHTML : '') + table.outerHTML + '</section>',
        hit: table.querySelectorAll('.timetable_con').length,
        mode: 'timetable-grid',
        meta: meta
      };
    }

    var parts = [], seen = [];
    for (var s = 0; s < SELS.length; s++) {
      var els = document.querySelectorAll(SELS[s]);
      for (var i = 0; i < els.length; i++) {
        var tr = els[i].closest('tr') || els[i];
        if (seen.indexOf(tr) >= 0) continue;
        seen.push(tr); parts.push(tr.outerHTML);
      }
    }
    if (parts.length < 2) {
      return {html: document.body.outerHTML, hit: parts.length, mode: 'fullpage', meta: meta};
    }
    return {html: '<table>' + parts.join('\\n') + '</table>', hit: parts.length,
            mode: 'selector', meta: meta};
    """
    return driver.execute_script(js, COURSE_SELECTORS)


def write_captured(html, meta):
    payload = {"html": html, "meta": meta,
               "capturedAt": time.strftime("%Y-%m-%d %H:%M:%S")}
    with open(CAPTURED_JS, "w", encoding="utf-8") as f:
        f.write("window.CAPTURED_SCHEDULE = " +
                json.dumps(payload, ensure_ascii=False) + ";\n")
    log(f"已写入抓取结果 → captured.js", "SUCCESS")


# ================= 主程序 =================
def main():
    print("=" * 56)
    print("   📅  西南大学课程表自动抓取器")
    print("=" * 56)

    # 1) 账号（本地已存则免输入，否则弹窗）
    if not resolve_account():
        return

    # 2) 代理探测
    proxy_on = setup_proxy()
    log(f"网络模式: {'走代理 '+PROXY if proxy_on else '直连（未检测到代理）'}", "SYSTEM")

    force_kill_chrome()

    driver = None
    try:
        # 3) 检测 Chrome 版本 + 下载匹配的跨平台 driver
        major = detect_chrome_version()
        if major:
            log(f"检测到 Chrome 主版本: {major}", "INFO")
        else:
            log("未能检测 Chrome 版本，将让 uc 自行决定", "WARN")
        driver_path = prepare_driver(major) if major else None

        options = uc.ChromeOptions()
        options.add_argument("--disable-popup-blocking")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")

        kw = {"options": options}
        if major:
            kw["version_main"] = major
        if driver_path:
            kw["driver_executable_path"] = driver_path
        driver = uc.Chrome(**kw)
        driver.set_window_size(1100, 850)

        # 登录（最多 3 次）
        ok = False
        for retry in range(1, 4):
            log(f"登录尝试 ({retry}/3)...", "INFO")
            if login_procedure(driver):
                ok = True
                break
            time.sleep(2)
        if not ok:
            log("三次登录均失败，请检查网络、代理或统一身份认证信息", "ERROR")
            log("如统一身份认证信息已变更，请删除 .env 后重试", "INFO")
            return

        # 进入课表页
        log("跳转课表页面...", "INFO")
        driver.get(URL_KEBIAO)
        time.sleep(4)

        # 正方将学年和学期分成两个独立选项；先查询用户实际选择的那一份课表。
        term_info = choose_academic_term(driver)
        title = term_info.get("timetableTitle", "")
        if title:
            log(f"当前课表：{title}", "SUCCESS")

        # 抓取
        log("正在抓取课表 ...", "INFO")
        res = grab_schedule_html(driver)
        hit = res.get("hit", 0)
        mode = res.get("mode", "?")
        html = res.get("html", "")

        if not html:
            log("未抓到任何内容，请确认课表页已加载", "ERROR")
            return
        if mode == "fullpage":
            log(f"未精确命中课程块（命中 {hit}），已抓整页，应用会尽力解析", "WARN")
        else:
            log(f"✓ 命中 {hit} 处课程节点", "SUCCESS")

        meta = {"hit": hit, "mode": mode, "pageUrl": driver.current_url}
        meta.update(res.get("meta") or {})
        write_captured(html, meta)

        # 打开可视化应用
        log("打开可视化应用 ...", "INFO")
        webbrowser.open("file://" + INDEX_HTML)
        print()
        log("✅ 完成！浏览器应用已自动载入课表。", "SUCCESS")
        log("   在应用里核对已自动读取的作息时间和第一周日期，即可导出 .ics", "INFO")
        print()

    except Exception as e:
        log(f"运行异常: {e}", "ERROR")
    finally:
        if driver:
            try:
                input("  按【回车】关闭抓取用 Chrome（应用页面不受影响）...")
            except (EOFError, KeyboardInterrupt):
                pass
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 已手动停止")
        force_kill_chrome()
        sys.exit(0)
