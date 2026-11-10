const { isIP } = require('node:net');

const COURSE_PARTITION = 'persist:course';
const configuredSessions = new WeakSet();

function isCourseURL(value, protocols = ['https:']) {
  try {
    const url = new URL(value);
    const host = url.hostname.replace(/\.$/, '');
    return protocols.includes(url.protocol) &&
      !url.username && !url.password && !isIP(host) &&
      /^[a-z0-9.-]+$/.test(host) && host.includes('.') &&
      !/(?:\.(localhost|local|internal)|(?:^|\.)home\.arpa)$/.test(host);
  } catch {
    return false;
  }
}

function configureCourseWebview(host, session) {
  const courseSession = session.fromPartition(COURSE_PARTITION);
  if (!configuredSessions.has(courseSession)) {
    configuredSessions.add(courseSession);
    courseSession.setPermissionRequestHandler((_contents, _permission, respond) => respond(false));
    courseSession.setPermissionCheckHandler(() => false);
    courseSession.on('will-download', (event) => event.preventDefault());
    courseSession.webRequest.onBeforeRequest(
      { urls: ['http://*/*', 'https://*/*', 'ws://*/*', 'wss://*/*'] },
      ({ url }, respond) => respond({ cancel: !isCourseURL(url, ['https:', 'wss:']) }),
    );
  }

  host.on('will-attach-webview', (event, preferences, params) => {
    if (params.partition !== COURSE_PARTITION || !isCourseURL(params.src)) {
      event.preventDefault();
      return;
    }
    delete preferences.preload;
    delete preferences.preloadURL;
    delete preferences.additionalArguments;
    Object.assign(preferences, {
      nodeIntegration: false,
      nodeIntegrationInWorker: false,
      nodeIntegrationInSubFrames: false,
      contextIsolation: true,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
      webviewTag: false,
      navigateOnDragDrop: false,
    });
  });

  host.on('did-attach-webview', (_event, guest) => {
    const restrictNavigation = (event) => {
      if (!isCourseURL(event.url)) event.preventDefault();
    };
    guest.on('will-frame-navigate', restrictNavigation);
    guest.on('will-redirect', restrictNavigation);
    guest.setWindowOpenHandler(() => ({ action: 'deny' }));
  });
}

module.exports = { configureCourseWebview, isCourseURL };
