; SRAN Desktop — custom NSIS hooks for the electron-builder installer.
;
; ── FIX20 §4: the install / reinstall crash, root-caused and fixed ─────────
;
; electron-builder's multiUser.nsh (setInstallModePerUser, run from .onInit)
; takes one of two paths. If HKCU\Software\${APP_GUID}\InstallLocation exists
; (an existing install) it uses that. Otherwise — every FIRST install and every
; reinstall after an uninstall — it calls SHGetKnownFolderPath and reads the
; returned string with
;     System::Call '*$2(&w${NSIS_MAX_STRLEN} .s)'
; i.e. a fixed 1024-wide-char (2 KB) read out of a ~90-byte CoTaskMem string.
; When the heap page after that string is unmapped the read faults:
; System.dll+0x1581, exception 0xC0000005. Reproduced in isolation with the
; same NSIS 3.0.4.1 / System.dll: the block alone crashes 18/20 runs; the same
; block with a bounded copy (lstrcpyW) crashes 0/20. It is probabilistic
; (heap layout), which is why FIX19 saw it on some fresh installs and not
; others, and never on an update over an existing install (that path skips
; the block). It never had anything to do with the user-data folder or with
; the customUnInstall step below.
;
; The block's result is discarded anyway (it sits inside System::Store S/L),
; so on Windows 8+ $INSTDIR always ends as "$LOCALAPPDATA\Programs\${APP_FILENAME}".
; Seeding InstallLocation with exactly that value makes every install take
; the safe "existing install" path. registryAddInstallInfo writes the same
; value after the files land; the uninstaller deletes the key. Guarded so the
; uninstaller-generation pass (BUILD_UNINSTALLER, which runs .onInit on the
; BUILD machine) never touches the build host's registry.
!macro preInit
  !ifndef BUILD_UNINSTALLER
    ReadRegStr $0 HKCU "${INSTALL_REGISTRY_KEY}" InstallLocation
    ${If} $0 == ""
      WriteRegStr HKCU "${INSTALL_REGISTRY_KEY}" InstallLocation "$LOCALAPPDATA\Programs\${APP_FILENAME}"
    ${EndIf}
  !endif
!macroend

; ── FIX19 §3: uninstall leaves no stale native-messaging registration ──────
;
; A registry key pointing at a deleted exe is exactly the leftover cruft the
; owner was bitten by. The sidecar registers the host com.sran.browser.electron
; under HKCU on first run; remove it here for Chrome and Edge.
;
; The user data folder (%APPDATA%\SranDesktop-Electron) is KEPT — electron-builder's
; deleteAppDataOnUninstall defaults to false and we leave it so. Deliberate:
; silently destroying a user's grants, connectors and notes is worse than a
; reinstall inheriting them.
;
; The Chrome extension was loaded UNPACKED by the user; an installer cannot
; remove it. The user must remove it from chrome://extensions themselves.
!macro customUnInstall
  DeleteRegKey HKCU "Software\Google\Chrome\NativeMessagingHosts\com.sran.browser.electron"
  DeleteRegKey HKCU "Software\Microsoft\Edge\NativeMessagingHosts\com.sran.browser.electron"
  DetailPrint "Removed native-messaging host registration (com.sran.browser.electron)."
!macroend
