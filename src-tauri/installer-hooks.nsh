;; Versions up to 0.9.1 kept a one-file core next to the app; the core now lives in core\.
!macro NSIS_HOOK_POSTINSTALL
  Delete "$INSTDIR\artist-core.exe"
!macroend
