# LLM Translation 1.2.0

本版重点：把文档转换引擎装进安装包，Windows 小白用户装完即可使用全部功能，
不再需要另装 Office、WPS 或 LibreOffice。

## 下载

- **Windows 10 / 11（64 位）安装包**：`LLMTranslation_Setup_1.2.0.exe`
- 免安装源码运行：克隆仓库后按 README 安装依赖，并把 Node + LibreOffice Kit
  放到 `vendor/libreoffice-kit/`

安装包按当前用户安装，不需要管理员权限、Python 或任何运行库。安装后从桌面或
开始菜单启动，填入自己的翻译 API Key 即可使用。

## 新增：内置 LibreOffice Kit

安装包现在包含：

- Node.js 24 运行时（`node.exe`）；
- LibreOffice Kit 0.1.5 及完整的 LibreOffice 引擎；
- 转换所需的全部 JS 依赖与上游许可文件。

引擎安装在 `C:\ProgramData\LLMTranslationKit`，而不是程序安装目录。这样即使
Windows 用户名或安装路径含中文，原生引擎也能正常启动；安装后自动生效，无需配置路径：

| 功能 | 之前（1.1.0） | 现在（1.2.0） |
| --- | --- | --- |
| DOCX 输入转 PDF | 需本机装 Word / WPS / LibreOffice | 内置引擎直接转换 |
| 目录页码回填 | 找不到转换器就没有页码 | 始终可用 |
| preview.pdf 预览 | 可能不生成 | 始终可用 |
| 图表页渲染 | Kit 路径只在作者机器上存在 | 内置引擎直接渲染 |

启动日志会显示“目录页码与 DOCX 转换：可用（内置 LibreOffice Kit）”。

如果用户自己配置了 `figures.node / figures.cli`，仍优先使用用户路径；路径失效时
自动回退到内置引擎。内置引擎不可用时，再回退到本机 LibreOffice / Word / WPS。

## 其他

- 安装体积约 380 MB，安装包约 106 MB（LZMA 压缩）；
- 卸载逻辑不变：可选保留配置和缓存，或全部删除；
- 翻译规则、缓存版本与 1.1.0 相同，**升级后已有译文缓存继续有效**，不会重复计费。

## 升级提示

直接运行 1.2.0 安装包覆盖安装即可，桌面快捷方式和设置都会保留。
`%LOCALAPPDATA%\LLMTranslation\settings.json` 中的服务商、API Key 和偏好不受影响。

## 系统要求

- Windows 10 / Windows 11（64 位），约 450 MB 磁盘空间
- 翻译需要网络和对应服务商的 API Key
- 不再需要 Microsoft Word、WPS 或 LibreOffice

## 已知限制

- 安装包未购买代码签名证书，SmartScreen 可能提示“未知发布者”
- 个别杀毒软件可能对体积较大的 `libreoffice-kit.exe` 误报，请添加信任
- 扫描版 PDF 需先做 OCR 得到可搜索 PDF
- 目录识别依赖原书目录页或 PDF 书签
- 表格按原版面渲染为图片嵌入，不在 Word 中重建单元格

## 许可证

- 项目源码：AGPL-3.0
- 运行时依赖：见仓库中的 `THIRD_PARTY_NOTICES.md`
- 内置 LibreOffice 引擎：MPL-2.0，许可全文与第三方清单随安装包附带
