# 第三方组件说明

本项目运行时会按需安装或下载以下第三方组件。它们分别遵循自身许可证；使用或再分发时，请同时遵守相应许可证的要求。

| 组件 | 用途 | 许可证 / 说明 |
|---|---|---|
| [Selenium](https://www.selenium.dev/) | 浏览器自动化 | Apache License 2.0 |
| [undetected-chromedriver](https://github.com/ultrafunkamsterdam/undetected-chromedriver) | Chrome 驱动封装 | GPL-3.0 |
| [ddddocr](https://github.com/sml2h3/ddddocr) | 本地验证码识别 | MIT License |
| [Pillow](https://python-pillow.org/) | 图像处理 | HPND License |
| [uv](https://github.com/astral-sh/uv) | 无系统 Python 时下载项目本地运行时 | Apache License 2.0 或 MIT License |

项目中的启动脚本只负责下载这些组件，不会把它们的二进制文件提交到仓库。首次运行后生成的 `.runtime/`、`drivers/` 等目录均已排除在 Git 之外。
