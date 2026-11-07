// The renderer's webPreferences, built in one place so a test can pin them
// (E1 §2, §4). No 'electron' import: this is a pure function over two inputs.
//
// Every flag here is the Electron equivalent of a control the WebView2 shell
// already proved (ELECTRON_SHELL_PLAN.md §4):
//   contextIsolation + nodeIntegration:false + sandbox  ≙ never AddHostObjectToScript
//   devTools off in release                            ≙ AreDevToolsEnabled=false in Release
//   webviewTag:false, no node in workers/subframes      ≙ no second way in

export interface WebPreferencesInput {
  /** Absolute path of the compiled preload script. */
  preloadPath: string;
  /** app.isPackaged — DevTools exist only when this is false. */
  isRelease: boolean;
}

export interface LockedWebPreferences {
  readonly contextIsolation: true;
  readonly nodeIntegration: false;
  readonly sandbox: true;
  readonly nodeIntegrationInWorker: false;
  readonly nodeIntegrationInSubFrames: false;
  readonly webviewTag: false;
  readonly webSecurity: true;
  readonly allowRunningInsecureContent: false;
  readonly navigateOnDragDrop: false;
  readonly devTools: boolean;
  readonly preload: string;
}

export function buildWebPreferences(input: WebPreferencesInput): LockedWebPreferences {
  return Object.freeze({
    contextIsolation: true,
    nodeIntegration: false,
    sandbox: true,
    nodeIntegrationInWorker: false,
    nodeIntegrationInSubFrames: false,
    webviewTag: false,
    webSecurity: true,
    allowRunningInsecureContent: false,
    navigateOnDragDrop: false,
    devTools: !input.isRelease,
    preload: input.preloadPath,
  } as const);
}
