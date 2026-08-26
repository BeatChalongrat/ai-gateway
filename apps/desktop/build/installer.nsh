; FIX19 — custom NSIS uninstall step for the SRAN Electron app.
;
; A clean uninstall must leave NO stale native-messaging registration behind:
; a registry key pointing at a deleted exe is exactly the leftover cruft the
; owner was just bitten by. The sidecar registers the host com.sran.browser.electron
; under HKCU on first run; remove it here for Chrome and Edge.
;
; The user data folder (%APPDATA%\SranDesktop-Electron) is KEPT — electron-builder's
; deleteAppDataOnUninstall defaults to false and we leave it so. Deliberate:
; silently destroying a user's grants, connectors and notes is worse than a
; reinstall inheriting them. See the report.
;
; The Chrome extension was loaded UNPACKED by the user; an installer cannot
; remove it. The user must remove it from chrome://extensions themselves.

!macro customUnInstall
  DeleteRegKey HKCU "Software\Google\Chrome\NativeMessagingHosts\com.sran.browser.electron"
  DeleteRegKey HKCU "Software\Microsoft\Edge\NativeMessagingHosts\com.sran.browser.electron"
  DetailPrint "Removed native-messaging host registration (com.sran.browser.electron)."
!macroend
