# LLM Translation 1.0.0

首个公开版本：把带文字层的英文 PDF / EPUB / DOCX 书籍翻译成排版好的中文 Word。

## 下载

- **Windows 10 / 11（64 位）安装包**：`LLMTranslation_Setup_1.0.0.exe`
- SHA256：`f41b79400290224ecf027c58eda4889de7ae191aa04ab5e3bd4f9df8ddca67ad`
- 免安装源码运行：克隆仓库后按 README 安装依赖

安装包约 32 MB，按当前用户安装，不需要管理员权限、Python 或任何运行库。
安装后从桌面或开始菜单启动，填入自己的翻译 API Key 即可使用。

## 主要功能

- 目录页优先：识别 `Contents` / `目录`、点线引导、右对齐页码，支持跨页与双栏；无目录页时改用 PDF 书签或版式推断标题层级
- 章节、脚注、文献、索引、表格、图版尽量保留原书结构
- 三套排版预设，生成带封面、目录、页码的校样级 DOCX
- 扫描件按页分级 OCR，只对疑似坏页调用视觉模型，控制成本
- 术语表、人名索引、AI 润色与审校，输出可审计的 `proof_report.md/csv`
- 译文磁盘缓存：同一文件重复翻译不重复消耗 token

## 安装 / 卸载

1. 双击 `LLMTranslation_Setup_1.0.0.exe`，按向导完成安装。
2. 卸载：设置 → 应用 → 已安装的应用 → LLM Translation。
3. 卸载时可选择“保留配置和缓存”或“全部删除”。

## 系统要求

- Windows 10 / Windows 11（64 位）
- 翻译需要网络和对应服务商的 API Key
- 可选：Microsoft Word、WPS 或 LibreOffice，用于生成 PDF 预览和目录页码回填

## 已知限制

- 安装包未购买代码签名证书，SmartScreen 可能提示“未知发布者”
- 扫描版 PDF 需先做 OCR 得到可搜索 PDF（软件会对疑似坏页做二次视觉识别，但不能替代整本 OCR）
- 目录识别依赖原书目录页或 PDF 书签；两者都缺失时按版式推断，章标题可能少于原书目录
- 表格按原版面渲染为图片嵌入，不在 Word 中重建单元格

## 许可证

- 项目源码：AGPL-3.0
- 运行时依赖：见仓库中的 `THIRD_PARTY_NOTICES.md`（PyMuPDF 为 AGPL-3.0 / 商业双许可）
