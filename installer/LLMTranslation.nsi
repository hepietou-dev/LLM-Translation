; LLM Translation - 一键安装/卸载脚本（NSIS 3）
Unicode true

!include "MUI2.nsh"
!include "nsDialogs.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"

!define APP_NAME "LLM Translation"
!define APP_VERSION "1.1.0"
!define APP_PUBLISHER "LLM Translation"
!define APP_EXE "LLMTranslation.exe"
!define APP_REG "Software\LLMTranslation"
!define UNINST_REG "Software\Microsoft\Windows\CurrentVersion\Uninstall\LLMTranslation"
!define DIST "..\dist\LLMTranslation"
!define ICON "..\assets\app.ico"

Name "${APP_NAME} ${APP_VERSION}"
OutFile "..\dist_installer\LLMTranslation_Setup_${APP_VERSION}.exe"
InstallDir "$LOCALAPPDATA\Programs\LLM Translation"
InstallDirRegKey HKCU "${APP_REG}" "InstallPath"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 16
SetDatablockOptimize on
CRCCheck on
BrandingText "${APP_NAME} ${APP_VERSION}"

VIProductVersion "1.1.0.0"
VIAddVersionKey "ProductName" "${APP_NAME}"
VIAddVersionKey "CompanyName" "${APP_PUBLISHER}"
VIAddVersionKey "FileDescription" "LLM Translation 安装程序"
VIAddVersionKey "FileVersion" "${APP_VERSION}.0"
VIAddVersionKey "ProductVersion" "${APP_VERSION}"
VIAddVersionKey "LegalCopyright" "Copyright (C) 2026 ${APP_PUBLISHER}"

!define MUI_ABORTWARNING
!define MUI_ICON "${ICON}"
!define MUI_UNICON "${ICON}"
!define MUI_WELCOMEPAGE_TITLE "安装 LLM Translation"
!define MUI_WELCOMEPAGE_TEXT "本程序会把英文 PDF / EPUB / DOCX 书籍翻译成中文 Word。$\r$\n$\r$\n安装不需要联网，不需要 Python 或任何运行库，约占 100 MB 磁盘空间。$\r$\n$\r$\n安装完成后，用桌面快捷方式启动，填入自己的翻译 API Key 即可使用。"
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "立即启动 LLM Translation"
!define MUI_FINISHPAGE_SHOWREADME "$INSTDIR\使用说明.txt"
!define MUI_FINISHPAGE_SHOWREADME_TEXT "查看使用说明"
!define MUI_FINISHPAGE_SHOWREADME_NOTCHECKED

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
UninstPage custom un.OptionsPage un.OptionsLeave
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH

!insertmacro MUI_LANGUAGE "SimpChinese"

Var KeepCfg

Function .onInit
  StrCpy $KeepCfg "1"
FunctionEnd

Function un.onInit
  StrCpy $KeepCfg "1"
  ${GetParameters} $R0
  ${GetOptions} $R0 "/DELETEALL" $R1
  ${IfNot} ${Errors}
    StrCpy $KeepCfg "0"
  ${EndIf}
FunctionEnd

Section "安装"
  SetShellVarContext current
  SetOutPath "$INSTDIR"
  File /r "${DIST}\*.*"
  File "使用说明.txt"

  WriteUninstaller "$INSTDIR\uninstall.exe"

  CreateDirectory "$SMPROGRAMS\LLM Translation"
  CreateShortcut "$SMPROGRAMS\LLM Translation\LLM Translation.lnk" "$INSTDIR\${APP_EXE}"
  CreateShortcut "$SMPROGRAMS\LLM Translation\使用说明.lnk" "$WINDIR\notepad.exe" '"$INSTDIR\使用说明.txt"'
  CreateShortcut "$DESKTOP\LLM Translation.lnk" "$INSTDIR\${APP_EXE}"

  WriteRegStr HKCU "${APP_REG}" "InstallPath" "$INSTDIR"
  WriteRegStr HKCU "${APP_REG}" "Version" "${APP_VERSION}"
  WriteRegStr HKCU "${UNINST_REG}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKCU "${UNINST_REG}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "${UNINST_REG}" "Publisher" "${APP_PUBLISHER}"
  WriteRegStr HKCU "${UNINST_REG}" "DisplayIcon" "$INSTDIR\${APP_EXE},0"
  WriteRegStr HKCU "${UNINST_REG}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_REG}" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegStr HKCU "${UNINST_REG}" "QuietUninstallString" '"$INSTDIR\uninstall.exe" /S'
  WriteRegDWORD HKCU "${UNINST_REG}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_REG}" "NoRepair" 1
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  WriteRegDWORD HKCU "${UNINST_REG}" "EstimatedSize" $0
SectionEnd

Function un.OptionsPage
  !insertmacro MUI_HEADER_TEXT "卸载选项" "请选择是否保留本机配置和翻译缓存。"
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}

  ${NSD_CreateLabel} 0 0 100% 28u "保留配置后，下次安装可以直接沿用服务商、API Key 和已翻译的缓存，不会重复计费。"
  Pop $0

  ${NSD_CreateRadioButton} 0 34u 100% 12u "保留配置和缓存（推荐）"
  Pop $R0
  ${NSD_CreateRadioButton} 0 54u 100% 12u "全部删除（偏好设置、翻译缓存）"
  Pop $R1

  ${If} $KeepCfg == "0"
    ${NSD_Check} $R1
  ${Else}
    ${NSD_Check} $R0
  ${EndIf}

  nsDialogs::Show
FunctionEnd

Function un.OptionsLeave
  ${NSD_GetState} $R0 $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $KeepCfg "1"
  ${Else}
    StrCpy $KeepCfg "0"
  ${EndIf}
FunctionEnd

Section "Uninstall"
  SetShellVarContext current
  Delete "$DESKTOP\LLM Translation.lnk"
  Delete "$SMPROGRAMS\LLM Translation\LLM Translation.lnk"
  Delete "$SMPROGRAMS\LLM Translation\使用说明.lnk"
  RMDir "$SMPROGRAMS\LLM Translation"

  DeleteRegKey HKCU "${UNINST_REG}"
  DeleteRegKey HKCU "${APP_REG}"

  RMDir /r "$INSTDIR"
SectionEnd

Function un.onUninstSuccess
  ${If} $KeepCfg != "1"
    RMDir /r "$LOCALAPPDATA\LLMTranslation"
  ${EndIf}
  RMDir "$INSTDIR"
FunctionEnd
