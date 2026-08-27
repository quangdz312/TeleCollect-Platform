const { app, BrowserWindow, dialog, ipcMain, Menu } = require('electron');
const { spawn, spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

let runtime = null;
let mainWindow = null;
let quitting = false;
let currentDataDir = null;

const desktopDir = __dirname;
const repoRoot = path.resolve(desktopDir, '..', '..');

function runtimeCommand(dataDir) {
  if (app.isPackaged) {
    return {
      command: path.join(process.resourcesPath, 'backend', 'TeleCollectBackend.exe'),
      args: ['--serve', '--data-dir', dataDir],
      cwd: path.join(process.resourcesPath, 'backend'),
    };
  }
  return {
    command: process.env.TELECOLLECT_PYTHON || path.join(repoRoot, '.venv', 'Scripts', 'python.exe'),
    args: ['-u', '-m', 'local_app.web_shell', '--serve', '--data-dir', dataDir],
    cwd: repoRoot,
  };
}

function configuredDataDir() {
  const marker = process.argv.indexOf('--data-dir');
  if (marker >= 0 && process.argv[marker + 1]) return process.argv[marker + 1];
  if (process.env.TELECOLLECT_DATA_DIR) return process.env.TELECOLLECT_DATA_DIR;
  const localAppData = process.env.LOCALAPPDATA || app.getPath('appData');
  const settingsPath = path.join(localAppData, 'TeleCollectLocal', 'settings.json');
  try {
    const settings = JSON.parse(fs.readFileSync(settingsPath, 'utf8'));
    if (typeof settings.data_dir === 'string' && settings.data_dir.trim()) {
      return settings.data_dir;
    }
  } catch (_) {
    // Missing or invalid settings means this is a first run.
  }
  return null;
}

async function initialDataDir() {
  const configured = configuredDataDir();
  if (configured) return configured;
  const result = await dialog.showOpenDialog({
    title: 'Choose a TeleCollect project folder',
    buttonLabel: 'Open project',
    defaultPath: app.getPath('documents'),
    properties: ['openDirectory', 'createDirectory'],
  });
  return result.canceled ? null : result.filePaths[0];
}

function terminateRuntime() {
  if (!runtime || runtime.exitCode !== null || runtime.killed) return;
  // The service owns backend and Next children.  Killing exactly this process
  // tree keeps a normal app exit from leaving loopback services behind.
  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/pid', String(runtime.pid), '/T', '/F'], { windowsHide: true });
  } else {
    runtime.kill('SIGTERM');
  }
}

function createWindow(url) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.loadURL(url);
    return;
  }
  mainWindow = new BrowserWindow({
    title: 'TeleCollect Local',
    width: 1400,
    height: 920,
    minWidth: 1080,
    minHeight: 720,
    webPreferences: {
      preload: path.join(desktopDir, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  mainWindow.on('closed', () => { mainWindow = null; });
  mainWindow.loadURL(url);
}

function showStartupFailure(details) {
  const safe = String(details).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
  createWindow(`data:text/html,<main style="font-family:Segoe UI,sans-serif;padding:32px"><h2>TeleCollect Local could not start</h2><p>See local app logs for details.</p><pre style="white-space:pre-wrap">${safe}</pre></main>`);
}

function startRuntime(dataDir) {
  const launch = runtimeCommand(dataDir);
  runtime = spawn(launch.command, launch.args, { cwd: launch.cwd, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  let output = '';
  const read = (chunk) => {
    output += chunk.toString();
    const match = output.match(/LOCAL_UI_URL=(http:\/\/127\.0\.0\.1:\d+)/);
    if (match) createWindow(match[1]);
  };
  runtime.stdout.on('data', read);
  runtime.stderr.on('data', read);
  runtime.on('error', (error) => showStartupFailure(`${error}\n\nRuntime: ${launch.command}`));
  runtime.on('exit', (code) => {
    if (!quitting && BrowserWindow.getAllWindows().length === 0) showStartupFailure(`${output}\nService exited with code ${code}.`);
  });
}

app.whenReady().then(async () => {
  // The local app has no native document/menu commands.  Hiding Electron's
  // default File/Edit/View menu keeps the window focused on TeleCollect.
  Menu.setApplicationMenu(null);
  ipcMain.handle('telecollect:choose-folder', async () => {
    const result = await dialog.showOpenDialog({ properties: ['openDirectory', 'createDirectory'] });
    return result.canceled ? null : result.filePaths[0];
  });
  currentDataDir = await initialDataDir();
  if (!currentDataDir) {
    app.quit();
    return;
  }
  createWindow('data:text/html,<main style="font-family:Segoe UI,sans-serif;display:grid;place-items:center;height:90vh;color:%23475569"><div><h2>Starting TeleCollect Local...</h2><p>Opening the selected project.</p></div></main>');
  startRuntime(currentDataDir);
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) startRuntime(currentDataDir); });
});

app.on('before-quit', () => { quitting = true; terminateRuntime(); });
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
