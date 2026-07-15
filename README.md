# 西南大学课程表导出工具

将西南大学教务系统中的个人课表导出为 `.ics` 日历文件，方便导入 iPhone、Mac、Google 日历、Outlook 等日历应用。

西南大学教务系统使用正方教务。本项目按西南大学当前课表页面的结构编写，不作为其他学校教务系统的通用抓取工具。

本项目为个人维护的开源工具，与西南大学及其教务系统没有隶属、授权或合作关系。

## 项目与联系

Copyright © 2026 Jiapeng Lee。项目主页：[awymp3/swu-schedule-export](https://github.com/awymp3/swu-schedule-export)。问题反馈或建议可发送至 [wadrqhh@gmail.com](mailto:wadrqhh@gmail.com)。

## 使用前说明

- 抓取课表时，需要连接西南大学校园网，或处于能够正常访问西南大学教务系统的校内网络环境。
- 首次运行需要联网下载浏览器驱动和 Python 依赖。若电脑没有可用的 Python 3，macOS 会将运行时下载到项目目录的 `.runtime` 中；Windows 会将运行时下载到 `%LOCALAPPDATA%\SWUScheduleExport\runtime` 的短路径中，以避免 Windows 的长路径限制。两者都不会修改系统 PATH。Windows 未安装 Chrome 时，会下载完整的 Chrome for Testing 到 `browsers/`，不会下载无头浏览器。
- 下载默认适配国内网络：依赖（包括 `ddddocr`）优先使用清华 PyPI 镜像，其次阿里云镜像；Windows 的本地 Python 使用阿里云 Python 镜像，macOS 的本地运行时优先使用可访问的 GitHub 发布镜像。镜像不可用时才回退官方源。
- 西南大学统一身份认证账号和密码只保存在项目目录的 `.env` 文件中，不会上传。该文件已被 Git 忽略，不应分享给其他人。

## 开始使用

### macOS

双击 `启动-Mac.command`。

如果系统提示文件无法打开，可在终端进入项目目录后执行：

```bash
chmod +x 启动-Mac.command
./启动-Mac.command
```

### Windows

双击 `启动-Windows.bat`。

脚本会优先使用已安装的 Python 3；未检测到时会下载项目本地运行时并继续执行。

### 运行流程

1. 第一次使用时输入西南大学统一身份认证账号和密码；Windows 会显示独立的图形登录窗口。勾选记住后，登录信息会写入 `.env`。
2. 程序登录教务系统后，输入要导出的学年和学期。直接按回车表示使用页面当前选中的学期。
3. 程序抓取课表，随后打开课表预览页。
4. 在预览页确认第一周周一日期；页面会自动读取课表中的节次时间，必要时可手动调整。
5. 点击“导出 .ics 日历”。

## 课表与日历内容

导出的每一节课会按照实际上课周次生成日历日程，单双周会分别处理。日程标题为课程名称，上课地点写入地点字段；教师、教学班、考核方式、选课备注、学时、学分等信息会写入日程备注。

导出文件名和日历名称会带上抓取到的学年、学期，避免不同学期的课表混在一起。

## 导入手机日历

### iPhone / iPad / Mac

将导出的 `.ics` 文件通过隔空投送、邮件、微信文件传输助手或“文件”App 发到设备上，点开该文件后选择“添加全部”或“添加到日历”。

### Android（Google 日历）

在电脑浏览器打开 [Google Calendar](https://calendar.google.com/)，依次进入“设置”→“导入和导出”→“导入”，选择导出的 `.ics` 文件和目标日历。导入完成后，手机上的 Google 日历会自动同步显示。

### 华为、小米、vivo、OPPO 等手机日历

不同系统的入口名称略有区别。通常可在手机日历的“设置”或“导入/导出”中选择 `.ics` 文件；若没有本地导入入口，使用上面的 Google 日历网页导入方式即可。

### Outlook

在 Outlook 的日历页面选择“添加日历”→“从文件上传”，再选择 `.ics` 文件。

## 常见问题

### 无法登录或课表页面打不开

先确认电脑已连接西南大学校园网，或当前网络能够访问 `jw.swu.edu.cn`。校外网络、代理设置或教务系统维护都可能导致登录失败。

### 需要导出其他学期

重新运行启动脚本，在“学年”和“学期”提示处输入对应值，例如 `2025-2026` 和 `2`。抓取后仍需按该学期的校历设置第一周周一日期。

### 需要更换统一身份认证账号

删除项目目录中的 `.env` 后重新运行，程序会再次要求输入统一身份认证账号和密码。

### 本地运行时或依赖下载失败

检查网络后重试。依赖下载会依次尝试清华、阿里云和官方 PyPI；Windows 本地 Python 优先从阿里云 Python 镜像下载。需要重新下载时，可删除项目目录中的 `.runtime`；账号信息不会受到影响。

## 文件说明

| 文件或目录 | 用途 |
|---|---|
| `启动-Mac.command` / `启动-Windows.bat` | macOS、Windows 启动脚本 |
| `capture_auto.py` | 登录、选择学年学期并抓取课表 |
| `capture.py` | 手动登录后抓取课表的备用脚本 |
| `index.html` | 课表预览与 `.ics` 导出页面 |
| `.env` | 本地统一身份认证信息，不会提交到 Git |
| `.env.example` | `.env` 格式示例，不含真实账号 |
| `.runtime/` | 自动下载的本地 Python 运行时，不会提交到 Git |
| `captured.js` | 最近一次抓取的课表数据，可删除 |
| `SECURITY.md` | 统一身份认证信息、数据和安全使用说明 |
| `THIRD_PARTY_NOTICES.md` | 运行时第三方组件及许可证说明 |

## 开源许可

本项目采用 [MIT License](LICENSE)。运行时下载的 Selenium、undetected-chromedriver、ddddocr、Pillow 和 uv 等组件分别适用各自的许可证，详情见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

使用前请阅读 [SECURITY.md](SECURITY.md)：不要提交 `.env`、`captured.js` 或其他包含统一身份认证信息、课程数据的本地文件。
