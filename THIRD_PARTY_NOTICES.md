# Third-party notices

The Windows installer bundles the following components. Their licenses apply to
those components; the project's own source is licensed separately (see LICENSE).

| Component | Version | License | Project |
| --- | --- | --- | --- |
| Python | 3.12 | Python Software Foundation License | https://www.python.org/ |
| PyMuPDF | 1.28.2 | GNU AGPL-3.0 or Artifex Commercial License | https://pymupdf.readthedocs.io/ |
| pypdf | 6.21.3 | BSD-3-Clause | https://pypdf.readthedocs.io/ |
| python-docx | 1.2.0 | MIT | https://python-docx.readthedocs.io/ |
| Pillow | 12.3.0 | MIT-CMU (HPND) | https://python-pillow.org/ |
| lxml | 6.1.0 | BSD-3-Clause | https://lxml.de/ |
| pycryptodome | 3.23.0 | BSD-2-Clause / public domain | https://www.pycryptodome.org/ |
| Tcl/Tk | 8.6 | BSD-style | https://www.tcl.tk/ |
| Node.js | 24.21.0 | MIT | https://nodejs.org/ |
| LibreOffice Kit | 0.1.5 | MPL-2.0 | https://github.com/deepseek-ai/dsh-libreoffice-kit |
| LibreOffice (bundled engine) | 25.x | MPL-2.0 | https://www.libreoffice.org/ |
| koffi | 3.1.1 | MIT | https://koffi.dev/ |
| fontkit | 2.0.4 | MIT | https://github.com/foliojs/fontkit |
| fflate | 0.8.2 | MIT | https://github.com/101arrowz/fflate |
| saxes | 6.0.0 | ISC | https://github.com/lddubeau/saxes |
| NSIS (build tool) | 3.11 | zlib/libpng | https://nsis.sourceforge.io/ |
| PyInstaller (build tool) | 6.22.3 | GPL-2.0-or-later with bootloader exception | https://pyinstaller.org/ |

安装包内的 `libreoffice-kit/` 目录保留上游自带的 LICENSE、NOTICE 与
LibreOffice 第三方许可清单（`licenses/LibreOffice-MPL-2.0.txt`、
`licenses/LibreOffice-third-party.html`），并附带 Node.js 的 MIT 许可全文。
LibreOffice 引擎的完整对应源码见 `libreoffice-kit/node_modules/@deepseek-ai/libreoffice-kit-win32-x64/sources/`
以及 https://github.com/deepseek-ai/dsh-libreoffice-kit 。

## PyMuPDF / AGPL

PyMuPDF is dual-licensed. This project uses it under the GNU Affero General
Public License v3.0. If you redistribute the installer or a modified version,
you must comply with the AGPL, including making the complete corresponding
source available. If AGPL terms are not acceptable, obtain a commercial
PyMuPDF license from Artifex and adjust the project license accordingly.

The PyInstaller bootloader exception means that applications frozen with
PyInstaller are not required to be GPL-licensed because of PyInstaller itself.

Full license texts are available from the upstream projects linked above.
